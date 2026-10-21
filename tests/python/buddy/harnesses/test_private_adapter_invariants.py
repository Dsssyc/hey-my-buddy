"""Real mock-controller executions keep attempt artifacts in private directories."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

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
from hey_my_buddy.protocol.process_identity import read_native_process_journal, native_process_journal_path
from hey_my_buddy.buddy.runtime.receipts import receipt_directory
from hey_my_buddy.errors import BoardError
from hey_my_buddy.buddy.roles import structured_call as read_only, turn_io
from hey_my_buddy.buddy.roles import run_execution
from hey_my_buddy.buddy.roles.controller import FastPreparation, ReviewPreparation, start_router_preparation
import buddy.harnesses.codex.test_no_tool_codex as codex_fast_tests
from buddy.harnesses.fixture_selection import select_fixture
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
            {"adapter": "codex", "harness": "codex", "provider": "openai", "model": "fixture-model", "effort": "low",
             "cwd": str(self.cwd), "routerBudgetSeconds": 3},
            self.root / "state" / "attempts" / "task" / f"attempt-{self.index}", {},
            {"HMB_STATE_DIR": str(self.root / "state"), "HMB_RUNTIME_ROOT": str(self.root / "runtime"),
             "HMB_DEV_SOURCE": "1"},
            session_record_path=str(self.root / f"session-{self.index}.jsonl"))
        request = NoToolStructuredRequest(str(self.cwd), "frozen original prompt", {"type": "object"}, 3)
        module = SimpleNamespace(check_preparation=lambda *_args: None, native_evidence=lambda _result: {})
        self.enterContext(mock.patch.dict(RUN_SEAMS, {"codex": module}))
        def execute(control, handle, state_dir):
            frame, _services, _observer, _correction = run_execution.fast_request(control, module)
            result = rc.RunResult(identity=frame.identity, harness="codex", end=rc.RunEnd(status="ok"),
                value=rc.RunValue(schema_status="unknown", raw="{}"),
                stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone")))
            return result, frame, {"stopReason": None, "elapsedMs": 1}, 0
        with mock.patch("hey_my_buddy.buddy.roles.inline_run.execute", side_effect=execute):
            handle = run_execution.start_fast(module, "codex", context, request)
            self.assertEqual(handle.wait(5), 0)
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
                    control_path.unlink(missing_ok=True)
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

    def assert_partition(self, context, harness):
        self.assertTrue(context.directory.is_dir())
        evidence_names = set()
        for path in context.directory.rglob("*"):
            if path.is_file():
                self.assertFalse(path.is_symlink(), path)
                evidence_names.add(path.name)
        if harness is not None:
            state = Path(context.environment["HMB_STATE_DIR"])
            native = native_root(state, harness, context.task_id)
            native_names = {path.name for path in native.rglob("*") if path.is_file()}
            self.assertFalse(evidence_names & native_names,
                             "native state files must stay out of execution evidence")

    def test_micro_task_mock_codex_zcode_claude_and_native_continuation(self):
        for kind, harness in ((codex_tests.CodexAdapterTests, "codex"), (zcode_tests.ZcodeFixtureCase, "zcode"),
                              (claude_tests.ClaudeAdapterTests, "claude")):
            with self.subTest(harness=harness):
                fixture = self.fixture(kind)
                context = fixture.context()
                state = Path(context.environment["HMB_STATE_DIR"])
                context.directory = state / "attempts" / context.task_id / context.attempt_id
                context.agent_credential = "private-fixture-credential"
                execution = fixture.execute(context)
                outcome = execution[1] if isinstance(execution, tuple) else execution
                self.assertEqual(outcome.status, "ok", outcome.to_report())
                self.assertTrue(outcome.shutdown_confirmed)
                private = context_root(context, harness)
                self.assertTrue((private / "agent-credential.json").is_file())
                cleanup_attempt_credentials(state, harness, context.task_id, context.attempt_id)
                self.assertFalse((private / "agent-credential.json").exists())
                if harness == "zcode":
                    self.assertFalse((private / "personal-provider.json").exists())
                    self.assertFalse((private / "finish-bridge.json").exists())
                    self.assertFalse((private / "inquiry.json").exists())
                if harness in {"codex", "zcode"}:
                    self.assertTrue(any(native_root(state, harness, context.task_id).glob("*.json")))
                self.assert_partition(context, harness)

    def test_mock_dsh_fast_router_and_command_use_private_attempt_directories(self):
        fixture = self.fixture(dsh_tests.DshNoToolTests)
        state = Path(fixture.environment["HMB_STATE_DIR"])
        evidence = state / "attempts/task/attempt-1"
        context = ExecutionContext("task", "attempt-1", 1,
            {"adapter": "dsh", "harness": "dsh", **dsh_tests.SPEC,
             "cwd": str(fixture.cwd), "routerBudgetSeconds": 3}, evidence, {}, fixture.environment,
            session_record_path=str(fixture.root / "session-attempt-1.jsonl"))
        request = NoToolStructuredRequest(str(fixture.cwd), "Choose a buddy", dsh_tests.SCHEMA, 3)
        handle = start_router_preparation(FastPreparation("dsh",  request, context, fixture.cwd))
        self.addCleanup(lambda: (handle.terminate(grace_seconds=0.1), handle.wait(15)) if not handle.finished else None)
        self.assertEqual(handle.wait(8), 0)
        outcome = read_only.collect(handle)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        control = handle.role_run_control
        self.assertTrue(Path(control["directory"]).is_relative_to(context_root(context, "dsh")))
        self.assertTrue((handle.no_tool_evidence.evidence_root / "call-1/result.json").is_file())
        self.assert_partition(context, "dsh")

        command_evidence = state / "attempts/command-task/attempt-2"
        command = ExecutionContext("command-task", "attempt-2", 1,
            {"adapter": "command", "cwd": str(fixture.cwd), "task": "fixture command", "expectedSeconds": 3,
             "argv": [sys.executable, "-c", "print('ok')"]}, command_evidence, {}, fixture.environment, session_record_path=None)
        adapter = CommandAdapter()
        child = adapter.start(command)
        self.addCleanup(lambda: child.terminate(grace_seconds=0.1) if child.group_alive() else None)
        self.assertEqual(child.wait(5), 0)
        self.assertEqual(adapter.collect(child, command).status, "ok")
        self.assert_partition(command, None)

    def test_codex_and_zcode_fast_router_calls_use_private_native_roots(self):
        for kind, adapter in ((codex_fast_tests.NoToolCodexTests, CodexAdapter),
                              (zcode_fast_tests.NoToolFakeProtocolTests, ZcodeAdapter)):
            with self.subTest(adapter=adapter.name):
                fixture = self.fixture(kind)
                if adapter.name == "codex":
                    environment = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "USER", "LOGNAME") if key in os.environ}
                    environment.update(HOME=str(fixture.root), CODEX_HOME=str(fixture.home),
                        PYTHONPATH=os.pathsep.join((str(Path(__file__).parents[4] / "src"), os.environ.get('PYTHONPATH', ''))), HMB_DEV_SOURCE="1",
                        HMB_CODEX_FIXTURE_STATE=str(fixture.root / "trace.json"),
                        HMB_STATE_DIR=str(fixture.root / "state"), HMB_RUNTIME_ROOT=str(fixture.root / "runtime"))
                    select_fixture(environment, fixture.root, "codex", codex_fast_tests.FIXTURE)
                    spec = {"provider": "openai", "model": "fixture-model", "effort": "low"}
                else:
                    environment = {**fixture.environment, "HMB_ZCODE_TEST_CASE": "ok"}
                    spec = {"provider": "fixture-api", "model": "fixture-model", "effort": "low"}
                evidence = Path(environment["HMB_STATE_DIR"]) / "attempts/task/attempt"
                context = ExecutionContext("task", "attempt", 1,
                    {"adapter": adapter.name, "harness": adapter.name, **spec, "cwd": str(fixture.cwd), "routerBudgetSeconds": 3},
                    evidence, {}, environment,
                    session_record_path=str(fixture.root / "session-attempt.jsonl"))
                native = adapter()
                request = NoToolStructuredRequest(str(fixture.cwd), "Pick a buddy", codex_fast_tests.SCHEMA, 3)
                handle = start_router_preparation(FastPreparation(native.name,  request, context, fixture.cwd))
                self.addCleanup(lambda h=handle: (h.terminate(grace_seconds=0.1), h.wait(15)) if not h.finished else None)
                self.assertEqual(handle.wait(8), 0)
                from hey_my_buddy.buddy.roles.structured_call import collect
                outcome = collect(handle)
                self.assertEqual(outcome.status, "ok", outcome.to_report())
                control = (handle.role_run_control if hasattr(handle, "role_run_control")
                           else json.loads((evidence / "no-tool-control.json").read_text()))
                self.assertTrue(Path(control["nativeRoot"]).is_relative_to(context_root(context, adapter.name)))
                cleanup_attempt_credentials(Path(environment["HMB_STATE_DIR"]), adapter.name, "task", "attempt")
                self.assert_partition(context, adapter.name)

    def test_review_read_only_calls_use_codex_private_root(self):
        fixture = self.fixture(codex_tests.CodexAdapterTests)
        context = fixture.context()
        account_home = fixture.root / "fixture-codex-home"
        account_home.mkdir(mode=0o700)
        (account_home / "auth.json").write_text('{"fixture":"login"}')
        context.environment["CODEX_HOME"] = str(account_home)
        context.directory = Path(context.environment["HMB_STATE_DIR"]) / "attempts" / context.task_id / context.attempt_id
        context.turn = None
        context.agent_credential = None
        # A review-mode call is a read-only structured invocation of the native
        # adapter; the private-root and partition invariants live on that path.
        request = ReadOnlyStructuredRequest(str(fixture.cwd), "Select from the frozen packet",
                                             router.answer_schema(["legal"]), router.budget())
        handle = start_router_preparation(ReviewPreparation("codex", CodexAdapter(), request, context,
                                                            (None, fixture.cwd, "fixture-digest")))
        self.addCleanup(lambda: (handle.terminate(grace_seconds=0.1), handle.wait(15)) if not handle.finished else None)
        self.assertIsNotNone(handle.wait(20))
        outcome = read_only.collect(handle)
        self.assertTrue(outcome.shutdown_confirmed, outcome.to_report())
        control = handle.role_run_control
        self.assertTrue(Path(control["nativeRoot"]).is_relative_to(context_root(context, "codex")))
        self.assertFalse((context_root(context, "codex") / "review-native/codex-home/auth.json").exists())
        cleanup_attempt_credentials(Path(context.environment["HMB_STATE_DIR"]), "codex", context.task_id, context.attempt_id)
        self.assert_partition(context, "codex")

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
                    env = {**fixture.environment, "HMB_ZCODE_TEST_CASE": "ok"}
                    spec = {"provider": "fixture-api", "model": "fixture-model", "effort": "low"}
                else:
                    env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "USER", "LOGNAME") if key in os.environ}
                    env.update(HOME=str(fixture.root), CODEX_HOME=str(fixture.home),
                        PYTHONPATH=os.pathsep.join((str(Path(__file__).parents[4] / "src"), os.environ.get('PYTHONPATH', ''))), HMB_DEV_SOURCE="1",
                        HMB_CODEX_FIXTURE_STATE=str(fixture.root / "trace.json"),
                        HMB_STATE_DIR=str(fixture.root / "state"), HMB_RUNTIME_ROOT=str(fixture.root / "runtime"))
                    select_fixture(env, fixture.root, "codex", codex_fast_tests.FIXTURE)
                    spec = {"provider": "openai", "model": "fixture-model", "effort": "low"}
                context = ExecutionContext("task", "attempt", 1,
                    {"adapter": "decision", "cwd": str(fixture.cwd), "routerBudgetSeconds": 60},
                    Path(env["HMB_STATE_DIR"]) / "attempts/task/attempt", {}, env,
                    session_record_path=str(fixture.root / "session-attempt.jsonl"),
                    decision_input={"buddy": {"harness": name, **spec},
                        "buddies": [{"buddyId": "legal"}], "routingMode": "fast",
                        "task": "Select", "outputSchema": router.answer_schema(["legal"]),
                        "budget": {"timeoutSeconds": 60}})
                decision_adapter = DecisionAdapter()
                decision_adapter.prepare(context)
                handle = decision_adapter.start(context)
                self.addCleanup(lambda h=handle: (h.terminate(grace_seconds=0.1), h.wait(15)) if not h.finished else None)
                self.assertIsNotNone(handle.wait(8))
                outcome = decision_adapter.collect(handle, context)
                self.assertTrue(outcome.shutdown_confirmed, outcome.to_report())
                control = (handle.role_run_control if hasattr(handle, "role_run_control")
                           else json.loads((context.directory / "no-tool-control.json").read_text()))
                self.assertTrue(Path(control["nativeRoot"]).is_relative_to(context_root(context, name)))
                cleanup_attempt_credentials(Path(env["HMB_STATE_DIR"]), name, "task", "attempt")
                self.assert_partition(context, name)

    def test_execution_entry_journals_each_offline_fast_router_native_tree(self):
        setups = ((dsh_tests.DshNoToolTests, "dsh"),
                  (zcode_fast_tests.NoToolFakeProtocolTests, "zcode"),
                  (codex_fast_tests.NoToolCodexTests, "codex"))
        for kind, harness in setups:
            with self.subTest(harness=harness):
                fixture = self.fixture(kind)
                if harness == "dsh":
                    fixture.select('{"buddyId":"legal","note":"fixture","evidence":[]}')
                    env = fixture.environment
                    buddy = {"harness": harness, **dsh_tests.SPEC}
                elif harness == "zcode":
                    env = {**fixture.environment, "HMB_ZCODE_TEST_CASE": "ok"}
                    buddy = {"harness": harness, "provider": "fixture-api",
                             "model": "fixture-model", "effort": "low"}
                else:
                    env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "USER", "LOGNAME") if key in os.environ}
                    env.update(HOME=str(fixture.root), CODEX_HOME=str(fixture.home),
                        PYTHONPATH=os.pathsep.join((str(Path(__file__).parents[4] / "src"), os.environ.get("PYTHONPATH", ""))),
                        HMB_DEV_SOURCE="1",
                        HMB_CODEX_FIXTURE_STATE=str(fixture.root / "trace.json"),
                        HMB_STATE_DIR=str(fixture.root / "state"), HMB_RUNTIME_ROOT=str(fixture.root / "runtime"))
                    select_fixture(env, fixture.root, "codex", codex_fast_tests.FIXTURE)
                    buddy = {"harness": harness, "provider": "openai",
                             "model": "fixture-model", "effort": "low"}
                state = Path(env["HMB_STATE_DIR"])
                state.mkdir(mode=0o700, parents=True, exist_ok=True)
                task_id, attempt_id = f"router-{harness}", f"attempt-{harness}"
                evidence = state / "attempts" / task_id / attempt_id
                evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
                claim = {"task": {"taskId": task_id, "spec": {
                    "adapter": "decision", "cwd": str(fixture.cwd), "routerBudgetSeconds": 5}},
                    "attempt": {"taskId": task_id, "attemptId": attempt_id, "generation": 1},
                    "decisionInput": {"buddy": buddy, "buddies": [{"buddyId": "legal"}],
                        "routingMode": "fast", "task": "Select", "requestId": task_id,
                        "outputSchema": router.answer_schema(["legal"]),
                        "budget": {"timeoutSeconds": 5}}}
                startup = receipt_directory(state, harness, task_id, attempt_id) / "execution-start.json"
                startup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                startup.write_text(json.dumps({"claim": claim, "stateDir": str(state),
                    "nonce": "n" * 64,
                    "sessionRecordPath": str(fixture.root / f"session-{harness}.jsonl")}))
                entry = fixture.root / f"execution-entry-{harness}.py"
                entry.write_text(
                    "import json, os, runpy\n"
                    "from pathlib import Path\n"
                    "from hey_my_buddy.protocol import client, process_identity\n"
                    "from hey_my_buddy.protocol.execution import fsync_json, read_execution_receipt\n"
                    "from hey_my_buddy.blackboard.service import harness_health\n"
                    f"state = Path({str(state)!r})\n"
                    f"identity_file = Path({str(fixture.root / 'execution-identity.json')!r})\n"
                    f"delivery = Path({str(fixture.root / 'execution-result.json')!r})\n"
                    "identity_file.write_text(json.dumps({'pid': os.getpid(), "
                    "'startedAt': process_identity.process_start(os.getpid(), include_exited=True)}))\n"
                    f"marker = state / 'attempts' / {task_id!r} / {attempt_id!r} / 'spawn.marker'\n"
                    f"fsync_json(marker, {{'taskId': {task_id!r}, 'attemptId': {attempt_id!r}, "
                    "'generation': 1, 'processIdentity': {'bootId': process_identity.boot_id(), "
                    "'pid': os.getpid(), 'startedAt': process_identity.process_start(os.getpid(), include_exited=True), "
                    "'processGroupId': os.getpgrp(), 'jobName': None}})\n"
                    "harness_health.HARNESSES = ()\n"
                    "class OfflineClient:\n"
                    "    def __init__(self, *args, **kwargs): pass\n"
                    "    def progress(self, *args, **kwargs): pass\n"
                    "    def submit_result(self, attempt_id, generation, nonce, reference):\n"
                    "        receipt = read_execution_receipt(Path(reference['path']).parent, "
                    f"binding={{'taskId': {task_id!r}, 'attemptId': attempt_id, 'generation': generation}}, "
                    "sha256=reference['sha256'])\n"
                    "        assert receipt is not None\n"
                    "        delivery.write_text(json.dumps(receipt['report']))\n"
                    "client.BoardClient = OfflineClient\n"
                    "runpy.run_module('hey_my_buddy.buddy.runtime.execution', run_name='__main__')\n"
                )
                completed = subprocess.run([sys.executable, str(entry), "--startup", str(startup)],
                    env=env, cwd=evidence, capture_output=True, text=True, timeout=35,
                    start_new_session=True)
                self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])
                delivery = fixture.root / "execution-result.json"
                self.assertTrue(delivery.is_file(), completed.stdout[-4000:])
                report = json.loads(delivery.read_text())
                self.assertIs(report["shutdownConfirmed"], True, report)
                root_identity = json.loads((fixture.root / "execution-identity.json").read_text())
                journal_path = native_process_journal_path(
                    receipt_directory(state, harness, task_id, attempt_id), root_identity)
                binding = {"taskId": task_id, "attemptId": attempt_id, "generation": 1}
                self.assertTrue(journal_path.is_file(), journal_path)
                native = read_native_process_journal(journal_path, binding)
                self.assertIsNotNone(native)
                self.assertEqual(native["pending"], 0, native)
                self.assertTrue(native["identities"], native)

    def test_dsh_coding_and_reconstructed_claude_continuation(self):
        fixture = self.fixture(dsh_coding_tests.DshRoleCase)
        original_record = fixture.micro_task_record
        def with_session_record(context, **kwargs):
            original_record(context, **kwargs)
            selection = json.loads(fixture.record.read_text())
            selection["dsh"]["command"] += ["--session-record", "usage"]
            fixture.record.write_text(json.dumps(selection))
        fixture.micro_task_record = with_session_record
        for index in (1, 2):
            with self.subTest(adapter="dsh", attempt=index):
                context = fixture.context(index=index, attempt=f"attempt-{index}")
                state = Path(context.environment["HMB_STATE_DIR"])
                context.directory = state / "attempts/task" / context.attempt_id
                handle, outcome = fixture.execute(context)
                self.assertEqual(outcome.status, "ok", outcome.to_report())
                control = handle.role_run_control
                self.assertEqual(set(control["inquiry"]), {"resultsPath"})
                self.assertNotIn("executionLiveFile", control)
                self.assertIsNone(handle.live_endpoint)
                self.assertEqual(Path(control["nativeRoot"]), native_root(state, "dsh", context.task_id))
                self.assertTrue((Path(control["nativeRoot"]) / "sessions").is_dir())
                request = handle.request
                self.assertFalse((Path(request.private_state.invocation_root) / "dsh-home").exists())
                self.assertTrue(Path(request.private_state.invocation_root).is_relative_to(context_root(context, "dsh")))
                records = list((Path(control["nativeRoot"]) / "sessions").rglob("*.v3.jsonl.zstd"))
                self.assertTrue(records, "the native rollout is in the shared task root")
                before_cleanup = {path: path.read_bytes() for path in records}
                cleanup_attempt_credentials(state, "dsh", "task", context.attempt_id)
                self.assertEqual({path: path.read_bytes() for path in records}, before_cleanup)
                self.assertFalse((context_root(context, "dsh") / "inquiry.json").exists())
                self.assertIsNotNone(handle.result)
                self.assert_partition(context, "dsh")

        claude = self.fixture(claude_tests.ClaudeAdapterTests)
        initial = claude.context()
        initial.directory = Path(initial.environment["HMB_STATE_DIR"]) / "attempts" / initial.task_id / initial.attempt_id
        first = claude.execute(initial)
        self.assertEqual(first.status, "ok", first.to_report())
        cleanup_attempt_credentials(Path(initial.environment["HMB_STATE_DIR"]), "claude", initial.task_id, initial.attempt_id)
        continuation = claude.context(index=2, previous=first.result["turn"]["sessionId"])
        continuation.directory = Path(continuation.environment["HMB_STATE_DIR"]) / "attempts" / continuation.task_id / continuation.attempt_id
        second = claude.execute(continuation)
        self.assertEqual(second.status, "ok", second.to_report())
        self.assertEqual(second.result["turn"]["resumeMode"], "reconstructed-new-session")
        cleanup_attempt_credentials(Path(continuation.environment["HMB_STATE_DIR"]), "claude", continuation.task_id, continuation.attempt_id)
        self.assert_partition(initial, "claude")
        self.assert_partition(continuation, "claude")

    def test_codex_and_zcode_native_continuation_survives_credential_cleanup(self):
        for kind, name in ((codex_tests.CodexAdapterTests, "codex"),
                           (zcode_tests.ZcodeFixtureCase, "zcode")):
            with self.subTest(adapter=name):
                fixture = self.fixture(kind)
                first = fixture.context()
                state = Path(first.environment["HMB_STATE_DIR"])
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
                self.assert_partition(first, name)
                self.assert_partition(second, name)


if __name__ == "__main__":
    unittest.main()
