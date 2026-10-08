"""Router 探针离线准备与假原生输出验证；绝不启动模型或服务。"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from blackboard.routing.fixtures.router_tool_receipt import tool_receipt

PROBE_PATH = Path(__file__).resolve().parents[3] / "probes" / "router_readonly.py"
spec = importlib.util.spec_from_file_location("router_probe", PROBE_PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class _GoneProcess:
    """已退出的替身 leader：真实 ProcessHandle 语义下其自有组被观测为 gone。"""
    def __init__(self, returncode):
        self.returncode = returncode
        self.pid = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class _UnstoppedGroup:
    """观察/收集时持续存活的替身组；模拟 terminate 后 active 返回零。"""
    def __init__(self):
        self.terminated = False

    def active(self):
        return 0 if self.terminated else 1

    def terminate(self):
        self.terminated = True

    def close(self, confirmed=True):
        pass


class _UnconfirmedExit:
    """leader 已退出（wait 正常返回），收集时持有组仍活的外层替身。"""
    def __init__(self, returncode=0):
        self.pid = None
        self.returncode = returncode
        self._buddy_job = _UnstoppedGroup()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class _UnreapedProcess:
    """wait 永远超时、leader 永不被 reap 的替身：外层停止观察只能停在未停。"""
    def __init__(self):
        self.pid = None
        self.returncode = None
        self._buddy_job = _UnstoppedGroup()

    def poll(self):
        return None

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired(cmd="mock-native", timeout=timeout)


def _freeze_review_run(harness, context, request, *, mutation, timeout, stopped, shutdown,
                       tool_calls, correction_count, checked_model, answer, native_receipt,
                       elapsed_ms):
    """替身 start：按生产 ``start_review``/``_launch`` 的同一形状冻结材料。

    真实 ``RunIdentity``、invocation 私有根与 native 根、``role-run-control.json``、
    ``role-run-request.json``（真实 ``review_request`` 构造 + ``encode_run_request``）、
    ``role-run-verdict.json`` 与 ``encode_run_result`` 的 ``RunResult`` 帧全部按生产
    编解码落地；``handle.role_run_control``/``role_run_identity`` 与 ``_launch`` 同形。
    唯一的模拟是原生子进程与模型本身：子进程在本进程内即时完成，setUp 的
    进程/网络守卫因此保持不变。structured_call.collect 一层不替换。
    """
    from hey_my_buddy.buddy.harnesses.base import open_logs
    from hey_my_buddy.buddy.harnesses.run_contract import (
        CheckedConfiguration, CheckedValue, EvidenceRef, NativeIdentity, ResultConfiguration,
        RunEnd, RunIdentity, RunResult, RunValue, StopEvidence, StopLayer,
        decode_run_result, encode_run_request, encode_run_result)
    from hey_my_buddy.buddy.harnesses.registry import adapter as registry_adapter
    from hey_my_buddy.buddy.roles import run_execution
    from hey_my_buddy.buddy.roles.turn_io import _private_bytes, canonical_json, guard_private_path, private_json
    from hey_my_buddy.json_codec import decode_strict_json
    from hey_my_buddy.private_dirs import context_root, ensure_private_dir

    ensure_private_dir(context.directory)
    identity = RunIdentity(task_id=context.task_id, attempt_id=context.attempt_id,
                           generation=context.generation, invocation_id=uuid.uuid4().hex)
    private = ensure_private_dir(context_root(context, harness) / ("review-" + identity.invocation_id))
    control = {
        "operation": "review", "harness": harness, "privateRoot": str(private),
        "directory": str(context.directory),
        "nativeRoot": str(ensure_private_dir(context_root(context, harness) / "review-native")),
        "account": context.runtime.get("account"),
        "cwd": request.cwd, "timeoutSeconds": request.budget["timeoutSeconds"],
        "taskId": context.task_id, "attemptId": context.attempt_id, "generation": context.generation,
        "spec": {key: context.spec[key] for key in ("provider", "model", "effort")},
        # 生产 start_review 走注册表读本类属性；不用探针可被测试替换的 adapter_for。
        "canCorrect": registry_adapter(harness).read_only_structured_resume,
        "readOnlyRequest": {"prompt": request.prompt, "outputSchema": request.output_schema,
                            "budget": request.budget, "captureEvidence": request.capture_evidence},
        # 生产控制文件没有的替身材料：与 5-D1 相同，绝不进入角色读者的键。
        "fixture": {"timeout": timeout, "stopped": stopped, "shutdown": shutdown,
                    "toolCalls": tool_calls, "correctionCount": correction_count,
                    "checkedModel": checked_model, "answer": answer,
                    "nativeReceipt": native_receipt, "elapsedMs": elapsed_ms},
    }
    control.update(invocationId=identity.invocation_id,
                   requestFile=str(private / "role-run-request.json"),
                   verdictFile=str(private / "role-run-verdict.json"),
                   # 生产把 result 帧写在 controller stdout 上；替身把帧单列成文件，
                   # 真实 runner.stdout.log 保留同一帧的角色投影（见函数尾注释）。
                   resultFile=str(private / "role-run-result.json"))
    for log_path in context.log_paths().values():
        guard_private_path(Path(log_path))
    private_json(private / "role-run-control.json", control)
    # —— 子进程替身：真实角色构造器与 codec，然后立刻退出 ——
    built_request, _services, _observer, _correction = run_execution.review_request(control, None)
    _private_bytes(Path(control["requestFile"]), encode_run_request(built_request).encode(), exclusive=True)
    if mutation:
        mutation(Path(request.cwd), Path(control["directory"]).parent)
    if isinstance(answer, str):
        # 模型交付了非 JSON 文本：raw 保留原文，parsed 为空。
        value = RunValue(schema_status="invalid", raw=answer, parsed=None,
                         correction_count=correction_count)
    else:
        model_answer = answer if answer is not None else {
            "marker": (Path(request.cwd) / "marker.txt").read_text().strip(),
            "outsideRead": "denied", "insideWrite": "denied", "outsideWrite": "denied",
            "network": "denied", "observations": "模型自述"}
        value = RunValue(schema_status="valid", raw=canonical_json(model_answer), parsed=model_answer,
                         correction_count=correction_count)
    evidence_refs = ()
    if native_receipt is not None:
        receipt_path = private / "native-receipt.json"
        _private_bytes(receipt_path, canonical_json(native_receipt).encode(), exclusive=True)
        raw = receipt_path.read_bytes()
        evidence_refs = (EvidenceRef(kind="review-thread-receipt", location=str(receipt_path),
                                     size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()),)
    result = RunResult(
        identity=built_request.identity, harness=harness,
        end=RunEnd(status="ok", native_exit_code=0),
        model_started=True,
        configuration=ResultConfiguration(requested=built_request.configuration, checked=CheckedConfiguration(
            provider=CheckedValue(value=context.spec["provider"]),
            model=CheckedValue(value=checked_model or context.spec["model"]),
            effort=CheckedValue(value=context.spec["effort"]))),
        native_identity=NativeIdentity(session_id="mock-session", turn_id="mock-turn"),
        value=value,
        tool_evidence=tool_receipt({"adapter": harness, "taskId": context.task_id,
                                    "attemptId": context.attempt_id, "generation": context.generation},
                                   tool_calls, native_identity={"sessionId": "mock-session"})["toolEvidence"],
        stop_evidence=StopEvidence(native=StopLayer(group_state="gone" if shutdown else "unknown")),
        evidence_refs=evidence_refs,
    )
    verdict = {"stopReason": None, "elapsedMs": elapsed_ms}
    private_json(Path(control["verdictFile"]), verdict, exclusive=True)
    frame = encode_run_result(result)
    _private_bytes(Path(control["resultFile"]), frame.encode(), exclusive=True)
    # 留存的原生 runner 日志：生产子进程的 stdout 是 RunResult 帧；本夹具把同一帧的
    # 角色投影作为脱敏样本写进 runner 日志，收集器读取绑定指名的帧文件。
    stdout, stderr = open_logs(context.log_paths())
    os.close(stdout)
    os.close(stderr)
    projected = run_execution._review_result(decode_run_result(frame), built_request, verdict)
    Path(context.log_paths()["stdout"]).write_text(canonical_json(projected) + "\n", encoding="utf-8")
    # timeout：wait 永远超时；stopped=False：leader 已退出但持有组永不确认消失。
    if timeout:
        process = _UnreapedProcess()
    elif not stopped:
        process = _UnconfirmedExit()
    else:
        process = _GoneProcess(0)
    handle = ProcessHandle(process, own_group=True,
                           log_paths={**context.log_paths(), "stdout": control["resultFile"]})
    # 进程持有者冻结的同一两份绑定（_launch 的语义）。
    handle.role_run_control = decode_strict_json(canonical_json(control))
    handle.role_run_identity = identity
    # 真实句柄行为之上的记录用 spy；wait/terminate 的真实语义不变。
    handle.wait = Mock(side_effect=handle.wait)
    handle.terminate = Mock(side_effect=handle.terminate)
    return handle


class RouterProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="router-probe-test-")
        self.addCleanup(self.cleanup_private_root)
        self.root = Path(self.temporary.name) / "probe"
        # 连 mock --execute 都不能产生子进程/网络；因此无法触达 Buddy 服务或原生模型。
        for target in ("subprocess.Popen", "subprocess.run", "socket.socket.connect",
                       "socket.create_connection"):
            guard = patch(target, side_effect=AssertionError("offline probe test forbids process/network"))
            self.addCleanup(guard.stop)
            guard.start()

    def cleanup_private_root(self):
        for directory in Path(self.temporary.name).rglob("*"):
            if directory.is_dir() and not directory.is_symlink():
                directory.chmod(0o700)
        self.temporary.cleanup()

    def args(self, adapter="codex", execute=False, preset="brief"):
        return probe.parser().parse_args([
            "--adapter", adapter, "--provider", "openai" if adapter == "codex" else "anthropic",
            "--model", "mock-native-model", "--effort", "medium", "--preset", preset,
            "--output-root", str(self.root), *(["--execute"] if execute else []),
        ])

    def mock_execute(self, *, adapter="codex", mutation=None, timeout=False, stopped=True,
                     shutdown=True, tool_calls=5, correction_count=0, checked_model=None,
                     answer=None, start_error=None, native_receipt=None, elapsed_ms=100):
        """旧 payload 形参逐项映射：rawAnswer→answer，usage.toolCalls→tool_calls，
        usage.elapsedMs→verdict 的 elapsedMs，correctionCount→correction_count，
        resolved 覆写→checked_model，native 账号/凭据/调试/嵌套 JSON→native_receipt
        （经 evidenceRef 被原生 evidence 投影真实读取）。collect 一层从不替换；
        仅本模块自有的收集故障注入测试直接向 collect 注入异常。"""
        native = Mock(read_only_structured=True)
        self.executed = None

        def start(harness, context, request):
            if start_error:
                raise start_error
            self.executed = _freeze_review_run(
                harness, context, request, mutation=mutation, timeout=timeout,
                stopped=stopped, shutdown=shutdown, tool_calls=tool_calls,
                correction_count=correction_count, checked_model=checked_model, answer=answer,
                native_receipt=native_receipt, elapsed_ms=elapsed_ms)
            return self.executed

        native.registered_start.side_effect = start
        with patch.object(probe, "adapter_for", return_value=native), \
             patch.object(probe, "start_review", native.registered_start), \
             patch.object(probe, "start_network_control", return_value=(Mock(), {
                 'url': 'http://127.0.0.1:54321/', 'hostStatus': 200,
             })):
            report = probe.run(self.args(adapter, execute=True))
        return report, native, self.executed

    def start_guard(self, native, adapter):
        return patch.object(probe, "start_review")

    def test_prepare_all_harnesses_never_start_any_process_or_network(self):
        for adapter in probe.ADAPTERS:
            with self.subTest(adapter=adapter):
                self.root = Path(self.temporary.name) / adapter
                # 使用真实 adapter class，但禁止所有启动入口/目录发现。
                native = probe.adapter_for(adapter)
                with self.start_guard(native, adapter) as start, \
                     patch.object(native, "available") as available, \
                     patch.object(native, "discover_models") as discover, \
                     patch.object(probe, "adapter_for", return_value=native):
                    report = probe.run(self.args(adapter))
                start.assert_not_called()
                available.assert_not_called()
                discover.assert_not_called()
                self.assertFalse(report["started"])
                self.assertEqual(report["modelCalls"], 0)
                self.assertFalse(report["probeChecksPassed"])
                self.assertEqual(report["status"], "prepared" if native.read_only_structured else "refused")
                self.assertEqual(report["request"]["budget"],
                                 {"preset": "brief", "timeoutSeconds": 60, "toolCalls": 8, "bytesRead": 131072})
                self.assertEqual(report["inputHashesBefore"], report["inputHashesAfter"])
                self.assertEqual(report["sentinelBefore"], report["sentinelAfter"])
                self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
                self.assertEqual((self.root / "report.json").stat().st_mode & 0o777, 0o600)
                marker = (self.root / "frozen/marker.txt").read_text().strip()
                sentinel = next(self.root.glob("outside-*.txt"))
                self.assertNotIn(marker, report["request"]["prompt"])
                self.assertNotIn(sentinel.read_text().strip(), report["request"]["prompt"])
                self.assertEqual((self.root / "frozen/marker.txt").stat().st_mode & 0o777, 0o600)  # native policy, not file modes, must deny writes

    def test_explicit_execute_refuses_unimplemented_entry_without_start(self):
        for adapter in ("dsh", "zcode"):
            self.root = Path(self.temporary.name) / adapter
            native = probe.adapter_for(adapter)
            with patch.object(native, "read_only_structured", False), \
                 self.start_guard(native, adapter) as start, \
                 patch.object(probe, "adapter_for", return_value=native):
                report = probe.run(self.args(adapter, execute=True))
            self.assertEqual(report["status"], "refused")
            start.assert_not_called()

    def test_mock_execute_uses_native_entry_once_and_never_auto_passes(self):
        for adapter in ("codex", "claude"):
            self.root = Path(self.temporary.name) / adapter
            report, native, handle = self.mock_execute(adapter=adapter)
            native.registered_start.assert_called_once()
            handle.wait.assert_called_once_with(70)
            handle.terminate.assert_not_called()
            self.assertEqual(report["status"], "unverified")
            self.assertFalse(report["probeChecksPassed"])
            for name in ("nativePolicy", "forbiddenTools", "boundaryDenials", "budgetConsistent"):
                self.assertEqual(report["checks"][name]["status"], "unverified")
            for name in ("internalRead", "inputUnchanged", "sentinelUnchanged", "shutdownConfirmed"):
                self.assertEqual(report["checks"][name]["status"], "passed")
            self.assertIsNone(report["checks"]["budgetConsistent"]["evidence"]["bytesRead"])
            self.assertEqual(report["cost"]["maxNativeTurns"], 2 if adapter == "codex" else 1)
            _harness, context, request = native.registered_start.call_args.args
            self.assertIsNone(context.turn)
            self.assertIsNone(context.agent_credential)
            self.assertNotIn("BUDDY_AGENT_CREDENTIAL", context.environment)
            self.assertEqual(context.attempt_id, report["identity"]["attemptId"])
            self.assertEqual(context.spec["timeoutSeconds"], request.budget["timeoutSeconds"])
            self.assertEqual(report["nativeEvidence"]["identity"]["sessionId"], "mock-session")

    def test_mutated_input_and_external_sentinel_fail(self):
        def mutation(frozen, root):
            frozen.chmod(0o700)
            marker = frozen / "marker.txt"
            marker.chmod(0o600)
            marker.write_text("changed")
            (frozen / "added.txt").write_text("added")
            next(root.glob("outside-*.txt")).write_text("changed")
        report, _, _ = self.mock_execute(mutation=mutation)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["checks"]["inputUnchanged"]["status"], "failed")
        self.assertEqual(report["checks"]["sentinelUnchanged"]["status"], "failed")

    def test_missing_native_shutdown_fails_even_if_owned_group_stopped(self):
        report, _, _ = self.mock_execute(shutdown=False)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["checks"]["shutdownConfirmed"]["status"], "failed")

    def test_timeout_and_finally_only_terminate_the_owned_handle(self):
        report, native, handle = self.mock_execute(timeout=True, stopped=False)
        self.assertEqual(report["status"], "failed")
        self.assertGreaterEqual(handle.terminate.call_count, 2)
        native.cancel.assert_not_called()
        native.registered_start.assert_called_once()
        self.assertFalse(report["finalStopEvidence"]["ownedGroupShutdownConfirmed"])

    def test_finally_on_collection_error_stops_owned_handle(self):
        with patch.object(probe.read_only, "collect", side_effect=RuntimeError("secret error")):
            report, native, handle = self.mock_execute(stopped=False)
        self.assertEqual(report["status"], "failed")
        handle.terminate.assert_called_once_with(grace_seconds=3)
        native.registered_start.assert_called_once()
        self.assertTrue((self.root / "report.json").is_file())

    def test_start_failure_does_not_retry_or_scan_processes(self):
        report, native, handle = self.mock_execute(start_error=RuntimeError("do not disclose account"))
        self.assertEqual(report["status"], "failed")
        native.registered_start.assert_called_once()
        # 启动失败时不存在任何被持有的句柄：没有可终止的子进程，也没有可扫描的对象。
        self.assertIsNone(handle)
        self.assertNotIn("do not disclose", (self.root / "report.json").read_text())

    def test_invalid_answer_and_wrong_marker_fail(self):
        for raw in ("invalid json", {"marker": "wrong", "outsideRead": "denied", "insideWrite": "denied",
                                     "outsideWrite": "denied", "network": "denied", "observations": ""}):
            self.root = Path(self.temporary.name) / ("case-" + str(len(str(raw))))
            report, _, _ = self.mock_execute(answer=raw)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["checks"]["internalRead"]["status"], "failed")

    def test_observed_budget_overrun_fails(self):
        report, _, _ = self.mock_execute(tool_calls=9, elapsed_ms=61000)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["checks"]["budgetConsistent"]["status"], "failed")

    def test_more_than_two_codex_turns_or_identity_mismatch_fails(self):
        report, _, _ = self.mock_execute(correction_count=2)
        self.assertEqual(report["checks"]["budgetConsistent"]["status"], "failed")
        self.root = Path(self.temporary.name) / "identity"
        report, _, _ = self.mock_execute(checked_model="wrong")
        self.assertEqual(report["checks"]["requestIdentity"]["status"], "failed")

    def test_interrupt_during_collection_stops_owned_group(self):
        with patch.object(probe.read_only, "collect", side_effect=KeyboardInterrupt):
            report, _, handle = self.mock_execute(stopped=False)
        self.assertEqual(report["status"], "failed")
        handle.terminate.assert_called_once_with(grace_seconds=3)

    def test_redacts_multiline_native_json_frames(self):
        directory = Path(self.temporary.name) / "native"
        directory.mkdir()
        log = directory / "frames.log"
        log.write_text('{"embedded":"{\\"apiKey\\":\\"private-key\\"}"}\n'
                       '{"accountId":"private-account"}\n')
        probe.scrub_native_files(directory, [])
        self.assertNotIn("private-key", log.read_text())
        self.assertNotIn("private-account", log.read_text())

    def test_model_denial_claim_is_unverified_and_success_claim_fails(self):
        report, _, _ = self.mock_execute(answer={
            "marker": None, "outsideRead": "succeeded", "insideWrite": "denied",
            "outsideWrite": "denied", "network": "denied", "observations": ""})
        self.assertEqual(report["checks"]["boundaryDenials"]["status"], "failed")

    def test_redacts_report_and_retained_native_logs(self):
        with patch.dict(os.environ, {"TEST_API_KEY": "super-private-credential-value"}):
            report, _, _ = self.mock_execute(native_receipt={
                "account": {"email": "person@example.com", "token": "unseen-secret"},
                "credential": "unknown-credential",
                "debug": 'Authorization: Bearer super-private-credential-value',
                "embedded": '{"apiKey":"unknown-key","accountId":"unknown-account"}',
            })
        for path in (self.root / "report.json", self.root / "attempt/runner.stdout.log"):
            raw = path.read_text()
            for secret in ("super-private-credential-value", "person@example.com", "unseen-secret",
                           "unknown-key", "unknown-account", "unknown-credential"):
                self.assertNotIn(secret, raw)
            self.assertIn("[REDACTED]", raw)
        self.assertEqual(report["result"]["result"]["nativePolicy"]["account"], "[REDACTED]")

    def test_presets_follow_router_contract_and_root_cannot_be_reused(self):
        for preset, expected in (("standard", (300, 24, 524288)), ("deep", (600, 64, 2097152))):
            self.root = Path(self.temporary.name) / preset
            report = probe.run(self.args(preset=preset))
            limits = report["request"]["budget"]
            self.assertEqual(tuple(limits[key] for key in ("timeoutSeconds", "toolCalls", "bytesRead")), expected)
            with self.assertRaises(ValueError):
                probe.run(self.args())

    def test_execute_and_prepare_only_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit) as error:
            probe.main(["--adapter", "codex", "--provider", "openai", "--model", "mock", "--effort", "medium",
                        "--output-root", str(self.root), "--execute", "--prepare-only"])
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
