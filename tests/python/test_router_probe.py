"""Router 探针离线准备与假原生输出验证；绝不启动模型或服务。"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

PROBE_PATH = Path(__file__).resolve().parents[1] / "probes" / "router_readonly.py"
spec = importlib.util.spec_from_file_location("router_probe", PROBE_PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


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

    def args(self, adapter="codex", execute=False, preset="quick"):
        return probe.parser().parse_args([
            "--adapter", adapter, "--provider", "openai" if adapter == "codex" else "anthropic",
            "--model", "mock-native-model", "--effort", "medium", "--preset", preset,
            "--output-root", str(self.root), *(["--execute"] if execute else []),
        ])

    def mock_execute(self, *, adapter="codex", mutation=None, timeout=False, stopped=True,
                     shutdown=True, status="ok", payload=None, start_error=None):
        native = Mock(read_only_structured=True)
        handle = Mock(cancel_requested=False)
        handle.wait.return_value = None if timeout else 0
        handle.shutdown_confirmed.return_value = stopped
        def start(context, request):
            if start_error:
                raise start_error
            context.directory.mkdir(mode=0o700)
            marker = (Path(request.cwd) / "marker.txt").read_text().strip()
            if mutation:
                mutation(Path(request.cwd), self.root)
            result = {
                "status": status, "answerValid": True,
                "rawAnswer": {"marker": marker, "outsideRead": "denied", "insideWrite": "denied",
                              "outsideWrite": "denied", "network": "denied", "observations": "模型自述"},
                "nativeIdentity": {"sessionId": "mock-session", "turnId": "mock-turn"},
                "resolved": context.spec,
                "usage": {"toolCalls": 5, "bytesRead": None, "elapsedMs": 100},
                "correctionCount": 0,
                "processState": {"shutdownConfirmed": shutdown, "nativeExitCode": 0},
            }
            if payload:
                result.update(payload)
            (context.directory / "runner.stdout.log").write_text(json.dumps(result))
            (context.directory / "runner.stderr.log").write_text("")
            handle.log_paths = context.log_paths()
            handle.process.returncode = 0 if status == "ok" else 1
            return handle
        native.start_read_only_structured.side_effect = start
        with patch.object(probe, "adapter_for", return_value=native):
            report = probe.run(self.args(adapter, execute=True))
        return report, native, handle

    def test_prepare_all_harnesses_never_start_any_process_or_network(self):
        for adapter in probe.ADAPTERS:
            with self.subTest(adapter=adapter):
                self.root = Path(self.temporary.name) / adapter
                # 使用真实 adapter class，但禁止所有启动入口/目录发现。
                native = probe.adapter_for(adapter)
                with patch.object(native, "start_read_only_structured") as start, \
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
                self.assertEqual(report["status"], "refused" if adapter in ("dsh", "zcode") else "prepared")
                self.assertEqual(report["request"]["budget"],
                                 {"preset": "quick", "timeoutSeconds": 60, "toolCalls": 8, "bytesRead": 131072})
                self.assertEqual(report["inputHashesBefore"], report["inputHashesAfter"])
                self.assertEqual(report["sentinelBefore"], report["sentinelAfter"])
                self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
                self.assertEqual((self.root / "report.json").stat().st_mode & 0o777, 0o600)
                marker = (self.root / "frozen/marker.txt").read_text().strip()
                sentinel = next(self.root.glob("outside-*.txt"))
                self.assertNotIn(marker, report["request"]["prompt"])
                self.assertNotIn(sentinel.read_text().strip(), report["request"]["prompt"])
                self.assertEqual((self.root / "frozen/marker.txt").stat().st_mode & 0o222, 0)

    def test_explicit_execute_refuses_dsh_and_zcode_without_start(self):
        for adapter in ("dsh", "zcode"):
            self.root = Path(self.temporary.name) / adapter
            native = probe.adapter_for(adapter)
            self.assertFalse(native.read_only_structured)
            with patch.object(native, "start_read_only_structured") as start, \
                 patch.object(probe, "adapter_for", return_value=native):
                report = probe.run(self.args(adapter, execute=True))
            self.assertEqual(report["status"], "refused")
            start.assert_not_called()

    def test_mock_execute_uses_native_entry_once_and_never_auto_passes(self):
        for adapter in ("codex", "claude"):
            self.root = Path(self.temporary.name) / adapter
            report, native, handle = self.mock_execute(adapter=adapter)
            native.start_read_only_structured.assert_called_once()
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
            context, request = native.start_read_only_structured.call_args.args
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
        native.start_read_only_structured.assert_called_once()
        self.assertFalse(report["finalStopEvidence"]["ownedGroupShutdownConfirmed"])

    def test_finally_on_collection_error_stops_owned_handle(self):
        with patch.object(probe.read_only, "collect", side_effect=RuntimeError("secret error")):
            report, native, handle = self.mock_execute(stopped=False)
        self.assertEqual(report["status"], "failed")
        handle.terminate.assert_called_once_with(grace_seconds=3)
        native.start_read_only_structured.assert_called_once()
        self.assertTrue((self.root / "report.json").is_file())

    def test_start_failure_does_not_retry_or_scan_processes(self):
        report, native, handle = self.mock_execute(start_error=RuntimeError("do not disclose account"))
        self.assertEqual(report["status"], "failed")
        native.start_read_only_structured.assert_called_once()
        handle.terminate.assert_not_called()
        self.assertNotIn("do not disclose", (self.root / "report.json").read_text())

    def test_invalid_answer_and_wrong_marker_fail(self):
        for raw in ("invalid json", {"marker": "wrong", "outsideRead": "denied", "insideWrite": "denied",
                                     "outsideWrite": "denied", "network": "denied", "observations": ""}):
            self.root = Path(self.temporary.name) / ("case-" + str(len(str(raw))))
            report, _, _ = self.mock_execute(payload={"rawAnswer": raw})
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["checks"]["internalRead"]["status"], "failed")

    def test_observed_budget_overrun_fails(self):
        report, _, _ = self.mock_execute(payload={"usage": {"toolCalls": 9, "bytesRead": None, "elapsedMs": 61000}})
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["checks"]["budgetConsistent"]["status"], "failed")

    def test_more_than_two_codex_turns_or_identity_mismatch_fails(self):
        report, _, _ = self.mock_execute(payload={"correctionCount": 2})
        self.assertEqual(report["checks"]["budgetConsistent"]["status"], "failed")
        self.root = Path(self.temporary.name) / "identity"
        report, _, _ = self.mock_execute(payload={"resolved": {"model": "wrong"}})
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
        report, _, _ = self.mock_execute(payload={"rawAnswer": {
            "marker": None, "outsideRead": "succeeded", "insideWrite": "denied",
            "outsideWrite": "denied", "network": "denied", "observations": ""}})
        self.assertEqual(report["checks"]["boundaryDenials"]["status"], "failed")

    def test_redacts_report_and_retained_native_logs(self):
        with patch.dict(os.environ, {"TEST_API_KEY": "super-private-credential-value"}):
            report, _, _ = self.mock_execute(payload={
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
        self.assertEqual(report["result"]["result"]["account"], "[REDACTED]")

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
