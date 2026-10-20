"""Runtime version diagnostics across the four real native run/catalog seams."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import errno
import os
import sys
import time
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import discovery, version
from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
from hey_my_buddy.protocol import owned_process as windows_process
from buddy.runtime.test_windows_process import FakeAPI, FakeProcess
from buddy.harnesses.claude import test_native_run as claude_cases
from buddy.harnesses.codex import test_native_run as codex_cases
from buddy.harnesses.dsh import test_native_run as dsh_cases
from buddy.harnesses.zcode import test_native_run as zcode_cases


CASES = {
    "claude": claude_cases.NativeRunCase,
    "codex": codex_cases.FastSeamTests,
    "zcode": zcode_cases.NativeRunCase,
    "dsh": dsh_cases.NativeRunCase,
}
PROCESS_HARNESSES = ("claude", "codex", "zcode")
STOPPED_REASONS = ("probe-failed", "timeout", "pipe-failed", "output-limit",
                   "launch-failed", "scan-timeout")


class RuntimeVersionTests(unittest.TestCase):
    @contextmanager
    def harness(self, name):
        # Existing fixtures provide private homes, accounts, launch patches and
        # protocols. Restore all environment changes between matrix entries.
        with mock.patch.dict(os.environ, dict(os.environ), clear=True):
            case = CASES[name]("runTest")
            try:
                case.setUp()
                if name == "dsh":
                    case.extra_agent_args = ["--prompt-mode", "final", "--final-answer", '{"choice":"a"}']
                yield case, {
                    "claude": claude_cases, "codex": codex_cases,
                    "zcode": zcode_cases, "dsh": dsh_cases,
                }[name].native_run
            finally:
                case.doCleanups()

    def execute(self, name, case, module):
        if name == "claude":
            request = case.request()
        elif name == "codex":
            request = case.request(permission="none", output_schema=codex_cases.FAST_SCHEMA,
                                   prompt="Pick a buddy", timeout=10)
        elif name == "zcode":
            request = case.fast_request(zcode_cases.structured_prompt("prompt", zcode_cases.SCHEMA), timeout=10)
        else:
            request = case.fast_request("prompt", timeout=10)
        return module.run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                          services=None, cancelled=lambda: False)

    def discover(self, name, case, module):
        if name == "zcode":
            os.environ["HMB_ZCODE_TEST_CASE"] = "discovery"
        return module.run_discovery(cwd=str(case.base / "cwd" if name == "dsh" else case.cwd),
                                    invocation_root=case.base / "version-discovery",
                                    native_root=case.base / "version-discovery-native",
                                    timeout_seconds=10, cancelled=lambda: False)

    @contextmanager
    def diagnostic(self, name, case, module, text=None, *, action="text"):
        """Change only synthetic CLI output or ACP agentInfo, not the owner stop."""
        if name == "dsh":
            agent = case.base / "version-agent.py"
            info = '"name": "fake-harness-acp"'
            if text is not None:
                info += ', "version": ' + repr(text)
            agent.write_text(dsh_cases.FAKE_AGENT.read_text().replace(
                '"name": "fake-harness-acp", "version": "0.0.1"', info))
            with mock.patch.object(module, "command_for", return_value=[
                    sys.executable, str(agent), "--log", str(case.agent_log_path), *case.extra_agent_args]):
                yield
            return
        fixture = {"claude": claude_cases.FIXTURE, "codex": codex_cases.FAST_FIXTURE,
                   "zcode": getattr(case, "fake", None)}[name]
        script = case.base / "version-cli.py"
        if name == "zcode":
            builtin = script.parent / "provider/zcode-builtin.json"
            builtin.parent.mkdir(exist_ok=True)
            builtin.write_text('{"config":{"providerConfigRules":{"templateRules":[],"providerRules":[]}}}')
        if action == "nonzero":
            body = "sys.exit(7)"
        elif action == "timeout":
            body = "time.sleep(60)"
        else:
            body = "print(" + repr(text or "") + ")"
        script.write_text("import os, sys, time\nif '--version' in sys.argv:\n    " + body +
                          "\n    sys.exit(0)\nos.execv(sys.executable, [sys.executable, " +
                          repr(str(fixture)) + ", *sys.argv[1:]])\n")
        with mock.patch.object(module, "cli_command", return_value=[sys.executable, str(script)]):
            yield

    @contextmanager
    def probe_result(self, reason=None, output=b"", *, error=None, created=True):
        """Inject a read/observation result through the actual public boundary.

        The original bounded reader reaps the fixture first. A false injected
        observation models unconfirmed evidence without leaving a live child.
        """
        if not created:
            public = discovery.probe

            def expired(command, environment, **kwargs):
                kwargs["deadline"] = time.monotonic() - 1
                return public(command, environment, **kwargs)

            with mock.patch.object(discovery, "probe", side_effect=expired):
                yield
            return
        if reason == "launch-failed":
            create = discovery.subprocess.Popen

            def refused(command, **kwargs):
                if "--version" in command:
                    raise OSError("synthetic version creation refused")
                return create(command, **kwargs)

            with mock.patch.object(discovery.subprocess, "Popen", side_effect=refused):
                yield
            return
        reader = discovery._probe_posix

        def observed(process, stop_at, deadline):
            _reason, _output, stopped = reader(process, stop_at, deadline)
            self.assertTrue(stopped, "the synthetic fixture itself must be reaped")
            if error is not None:
                raise error
            return reason, output, reason != "shutdown-unverified"

        with mock.patch.object(discovery, "_probe_posix", side_effect=observed):
            yield

    def assert_continued(self, result, expected_version):
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.harness_version, expected_version)
        if expected_version is None:
            self.assertIsNone(result.harness_version)
        else:
            self.assertIsInstance(result.harness_version, str)
        self.assertTrue(result.model_started)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def assert_refused(self, result):
        self.assertEqual((result.end.status, result.end.reason_code), ("error", "native-shutdown-failed"))
        self.assertEqual(result.stop_evidence.native.group_state, "unknown")
        self.assertFalse(result.model_started)
        self.assertIsNone(result.harness_version)

    def test_four_harnesses_report_version_or_none_and_continue(self):
        for name in CASES:
            for text in ("1.2.3", None):
                with self.subTest(harness=name, text=text), self.harness(name) as (case, module), \
                        self.diagnostic(name, case, module, text):
                    self.assert_continued(self.execute(name, case, module), text)

    def test_four_harnesses_normalize_empty_and_unknown_diagnostics(self):
        for name in CASES:
            for text in ("", " \n ", "unknown"):
                with self.subTest(harness=name, text=text), self.harness(name) as (case, module), \
                        self.diagnostic(name, case, module, text):
                    self.assert_continued(self.execute(name, case, module), None)

    def test_four_catalogs_render_unknown_only_at_the_catalog_boundary(self):
        for name in CASES:
            for text in ("1.2.3", None):
                with self.subTest(harness=name, text=text), self.harness(name) as (case, module), \
                        self.diagnostic(name, case, module, text):
                    catalog = self.discover(name, case, module)
                    self.assertEqual(catalog["harnessVersion"], text or "unknown")
                    self.assertTrue(catalog["providers"])

    def test_three_version_processes_share_the_public_bounded_probe_in_both_seams(self):
        for name in PROCESS_HARNESSES:
            for operation in (self.execute, self.discover):
                with self.subTest(harness=name, seam=operation.__name__), self.harness(name) as (case, module), \
                        mock.patch.object(module, "probe_version", wraps=version.probe_version) as shared, \
                        mock.patch.object(discovery, "probe", wraps=discovery.probe) as public:
                    result = operation(name, case, module)
                    if operation == self.execute:
                        self.assert_continued(result, result.harness_version)
                        self.assertIsInstance(result.harness_version, str)
                    else:
                        self.assertIsInstance(result["harnessVersion"], str)
                    shared.assert_called_once()
                    public.assert_called_once()
                    self.assertEqual(public.call_args.args[0][-1], "--version")
                    self.assertLessEqual(public.call_args.kwargs["deadline"], time.monotonic() + 5)
                    self.assertEqual(public.call_args.kwargs["cwd"], str(case.cwd))

    def test_three_real_nonzero_version_processes_continue_run_and_catalog(self):
        for name in PROCESS_HARNESSES:
            for operation in (self.execute, self.discover):
                with self.subTest(harness=name, seam=operation.__name__), self.harness(name) as (case, module), \
                        self.diagnostic(name, case, module, action="nonzero"):
                    result = operation(name, case, module)
                    if operation == self.execute:
                        self.assert_continued(result, None)
                    else:
                        self.assertEqual(result["harnessVersion"], "unknown")

    def test_three_real_timed_out_version_processes_continue_after_public_stop(self):
        for name in PROCESS_HARNESSES:
            with self.subTest(harness=name), self.harness(name) as (case, module), \
                    self.diagnostic(name, case, module, action="timeout"), \
                    mock.patch.object(discovery, "_TIMEOUT", 0.05):
                self.assert_continued(self.execute(name, case, module), None)

    def test_stopped_probe_failures_continue_in_both_seams(self):
        for name in PROCESS_HARNESSES:
            for reason in STOPPED_REASONS:
                for operation in (self.execute, self.discover):
                    with self.subTest(harness=name, reason=reason, seam=operation.__name__), \
                            self.harness(name) as (case, module), \
                            self.probe_result(reason, b"ignored"):
                        result = operation(name, case, module)
                        if operation == self.execute:
                            self.assert_continued(result, None)
                        else:
                            self.assertEqual(result["harnessVersion"], "unknown")

    def test_public_receipt_distinguishes_no_creation_and_stopped_diagnostic_children(self):
        for name in PROCESS_HARNESSES:
            for reason, created in ((None, True), ("launch-failed", False),
                                    ("scan-timeout", False), ("scan-timeout", True),
                                    ("probe-failed", True)):
                with self.subTest(harness=name, reason=reason, created=created), \
                        self.harness(name) as (case, module):
                    receipts = []

                    def capture(*args, **kwargs):
                        result = version.probe_version(*args, **kwargs)
                        receipts.append(dict(kwargs["owned"][0]))
                        return result

                    with self.probe_result(reason, b"1.2.3", created=created or reason == "launch-failed"), \
                            mock.patch.object(module, "probe_version", side_effect=capture):
                        result = self.execute(name, case, module)
                    self.assert_continued(result, "1.2.3" if reason is None else None)
                    self.assertEqual(receipts, [{"created": created, "shutdown": True,
                                                "exit_code": 0 if created else None}])

    def test_confirmed_read_exceptions_continue_in_both_seams(self):
        for name in PROCESS_HARNESSES:
            for operation in (self.execute, self.discover):
                error = ValueError("synthetic version read failure")
                error.discovery_shutdown_confirmed = True
                with self.subTest(harness=name, seam=operation.__name__), self.harness(name) as (case, module), \
                        self.probe_result(error=error):
                    result = operation(name, case, module)
                    if operation == self.execute:
                        self.assert_continued(result, None)
                    else:
                        self.assertEqual(result["harnessVersion"], "unknown")

    def test_unconfirmed_version_stop_refuses_run_without_starting_main_process(self):
        for name in PROCESS_HARNESSES:
            with self.subTest(harness=name), self.harness(name) as (case, module), \
                    self.probe_result("shutdown-unverified", b"1.2.3"), \
                    mock.patch.object(module, "owned_popen") as main:
                self.assert_refused(self.execute(name, case, module))
                main.assert_not_called()

    def test_unconfirmed_version_stop_keeps_false_in_catalog_error_and_owned_receipt(self):
        for name in PROCESS_HARNESSES:
            with self.subTest(harness=name), self.harness(name) as (case, module), \
                    self.probe_result("shutdown-unverified"), \
                    mock.patch.object(module, "probe_version", wraps=version.probe_version) as shared, \
                    mock.patch.object(module, "owned_popen") as main:
                with self.assertRaises(version.VersionShutdownError) as caught:
                    self.discover(name, case, module)
                main.assert_not_called()
                self.assertEqual(caught.exception.code, "native-shutdown-failed")
                self.assertIs(caught.exception.discovery_shutdown_confirmed, False)
                self.assertEqual(shared.call_args.kwargs["owned"],
                                 [{"created": True, "shutdown": False, "exit_code": 0}])

    def test_unconfirmed_read_exception_refuses_both_seams_and_preserves_cause(self):
        for name in PROCESS_HARNESSES:
            for stopped in (False, None):
                for operation in (self.execute, self.discover):
                    error = ValueError("synthetic version read failure")
                    if stopped is not None:
                        error.discovery_shutdown_confirmed = stopped
                    with self.subTest(harness=name, stopped=stopped, seam=operation.__name__), \
                            self.harness(name) as (case, module), \
                            self.probe_result(error=error), \
                            mock.patch.object(module, "probe_version", wraps=version.probe_version) as shared, \
                            mock.patch.object(module, "owned_popen") as main:
                        if operation == self.execute:
                            self.assert_refused(operation(name, case, module))
                        else:
                            with self.assertRaises(version.VersionShutdownError) as caught:
                                operation(name, case, module)
                            self.assertIs(caught.exception.__cause__, error)
                            self.assertIs(caught.exception.discovery_shutdown_confirmed, False)
                        main.assert_not_called()
                        self.assertIs(shared.call_args.kwargs["owned"][0]["shutdown"], False)

    def claude_main_close_failure(self, *, stopped, stop_error=False):
        for operation in (self.execute, self.discover):
            with self.subTest(seam=operation.__name__), self.harness("claude") as (case, module):
                create, close, halt = module.owned_popen, os.close, module._halt_owned_group
                children, failed_fds = [], []
                fault = OSError(errno.EIO, "synthetic post-create stderr close failed")
                halt_fault = OSError(errno.EIO, "synthetic stop observation failed")
                pending_fd = None

                def held_create(*args, **kwargs):
                    nonlocal pending_fd
                    child = create(*args, **kwargs)
                    pending_fd = kwargs["stderr"]
                    children.append((child, module.ProcessHandle(child, own_group=True, log_paths={})))
                    return child

                def close_failed(fd):
                    nonlocal pending_fd
                    if fd == pending_fd:
                        pending_fd = None
                        # The owner must already hold the actual main process,
                        # not the earlier version process's confirmed receipt.
                        failed_fds.append(fd)
                        receipt = shared.call_args.kwargs["owned"][0]
                        self.assertIsInstance(receipt, module.ProcessHandle)
                        self.assertIs(receipt.process, children[0][0])
                        raise fault
                    return close(fd)

                def halt_observed(process, handle, deadline):
                    self.assertIs(shared.call_args.kwargs["owned"][0], handle)
                    actual, signalled = halt(process, handle, deadline)
                    self.assertTrue(actual, "the real private fixture must be reaped first")
                    if stop_error:
                        raise halt_fault
                    return actual if stopped else False, signalled

                try:
                    with mock.patch.object(module, "probe_version", wraps=version.probe_version) as shared, \
                            mock.patch.object(module, "owned_popen", side_effect=held_create) as main, \
                            mock.patch.object(module.os, "close", side_effect=close_failed), \
                            mock.patch.object(module, "_halt_owned_group", side_effect=halt_observed) as stop, \
                            mock.patch.object(module, "Connection") as connection:
                        if operation == self.execute:
                            result = operation("claude", case, module)
                            self.assertEqual((result.end.status, result.end.reason_code),
                                             ("error", "invalid-native-result"))
                            self.assertEqual(result.stop_evidence.native.group_state,
                                             "gone" if stopped and not stop_error else "unknown")
                            self.assertFalse(result.model_started)
                        else:
                            with self.assertRaises(OSError) as caught:
                                operation("claude", case, module)
                            self.assertIs(caught.exception, halt_fault if stop_error else fault)
                            self.assertIs(caught.exception.discovery_shutdown_confirmed,
                                          stopped and not stop_error)
                        main.assert_called_once()
                        connection.assert_not_called()
                        stop.assert_called_once()
                        receipt = shared.call_args.kwargs["owned"][0]
                        self.assertEqual(len(failed_fds), 1)
                        self.assertIsNotNone(children[0][0].poll())
                        self.assertTrue(children[0][0].stdout.closed)
                        if stop_error:
                            self.assertIsInstance(receipt, module.ProcessHandle)
                        else:
                            self.assertEqual(receipt, {"created": True, "shutdown": stopped,
                                                      "exit_code": children[0][0].returncode})
                finally:
                    # This independent holder also reaps a child if a mutant
                    # drops registration or skips the production stop entirely.
                    for child, handle in children:
                        try:
                            confirmed, _signalled = halt(child, handle, time.monotonic() + 5)
                            self.assertTrue(confirmed)
                        finally:
                            for stream in (child.stdin, child.stdout):
                                stream.close()
                    for fd in failed_fds:
                        close(fd)

    def test_claude_main_log_close_failure_keeps_confirmed_stop(self):
        self.claude_main_close_failure(stopped=True)

    def test_claude_main_log_close_failure_keeps_unconfirmed_stop(self):
        self.claude_main_close_failure(stopped=False)

    def test_claude_cleanup_error_keeps_registered_main_stop_unknown(self):
        self.claude_main_close_failure(stopped=False, stop_error=True)

    def test_claude_creation_failures_consume_creating_side_receipts(self):
        # These are the real owned_popen producer's Windows branches with the
        # existing simulated API/process fixture, not Windows hardware evidence.
        for created, stopped in ((False, True), (True, True), (True, False)):
            for operation in (self.execute, self.discover):
                with self.subTest(created=created, stopped=stopped, seam=operation.__name__), \
                        self.harness("claude") as (case, module):
                    process = FakeProcess()
                    api = FakeAPI(process)
                    api.fail_at = "assign"
                    real_create = windows_process.subprocess.Popen
                    errors = []

                    def popen(argv, **kwargs):
                        if "--version" in argv:
                            return real_create(argv, **kwargs)
                        self.assertTrue(created)
                        self.assertEqual(kwargs["creationflags"] & windows_process.CREATE_SUSPENDED,
                                         windows_process.CREATE_SUSPENDED)
                        return process

                    def producer(*args, **kwargs):
                        try:
                            return windows_process.owned_popen(*args, windows_api=api, **kwargs)
                        except BaseException as error:
                            errors.append(error)
                            raise

                    with mock.patch.object(module, "probe_version", wraps=version.probe_version) as shared, \
                            mock.patch.object(module, "owned_popen", side_effect=producer) as main, \
                            mock.patch.object(windows_process.subprocess, "Popen", side_effect=popen), \
                            mock.patch.object(module, "Connection") as connection, \
                            mock.patch.object(module, "_halt_owned_group") as stop:
                        if not created:
                            failure = mock.patch.object(api, "create_job", side_effect=OSError("synthetic job creation refused"))
                        elif not stopped:
                            failure = mock.patch.object(process, "kill", side_effect=OSError("synthetic cleanup unknown"))
                        else:
                            failure = nullcontext()
                        with failure:
                            if operation == self.execute and stopped:
                                result = operation("claude", case, module)
                                self.assertEqual((result.end.status, result.end.reason_code),
                                                 ("error", "harness-unavailable"))
                                self.assertEqual(result.stop_evidence.native.group_state, "gone")
                                self.assertFalse(result.model_started)
                            else:
                                error_type = module.ClaudeProtocolError if stopped else RuntimeError
                                with self.assertRaises(error_type) as caught:
                                    operation("claude", case, module)
                                if operation == self.discover:
                                    self.assertIs(caught.exception.discovery_shutdown_confirmed, stopped)
                                if not stopped:
                                    self.assertIs(caught.exception, errors[0])
                                    self.assertEqual(caught.exception.owned_popen_evidence,
                                                     windows_process.ProcessCreationEvidence(True, False, None))
                        main.assert_called_once()
                        connection.assert_not_called()
                        stop.assert_not_called()
                        expected = windows_process.ProcessCreationEvidence(
                            created, stopped, 1 if created and stopped else None)
                        self.assertEqual(errors[0].owned_popen_evidence, expected)
                        self.assertEqual(shared.call_args.kwargs["owned"],
                                         [{"created": created, "shutdown": stopped, "exit_code": expected.exit_code}])

    def test_claude_connection_failure_replaces_the_stopped_version_receipt(self):
        for operation in (self.execute, self.discover):
            with self.subTest(seam=operation.__name__), self.harness("claude") as (case, module), \
                    mock.patch.object(module, "Connection", side_effect=ValueError("synthetic connection failure")), \
                    mock.patch.object(module.ProcessHandle, "shutdown_confirmed", return_value=False):
                if operation == self.execute:
                    result = operation("claude", case, module)
                    self.assertEqual(result.end.reason_code, "invalid-native-result")
                    self.assertEqual(result.stop_evidence.native.group_state, "unknown")
                else:
                    with self.assertRaises(ValueError) as caught:
                        operation("claude", case, module)
                    self.assertIs(caught.exception.discovery_shutdown_confirmed, False)

    def test_dsh_missing_version_uses_one_initialize_and_no_extra_version_process(self):
        for operation in (self.execute, self.discover):
            with self.subTest(seam=operation.__name__), self.harness("dsh") as (case, module), \
                    self.diagnostic("dsh", case, module), \
                    mock.patch.object(module.AcpClient, "start", wraps=module.AcpClient.start) as start, \
                    mock.patch.object(discovery, "probe") as public:
                result = operation("dsh", case, module)
                if operation == self.execute:
                    self.assert_continued(result, None)
                else:
                    self.assertEqual(result["harnessVersion"], "unknown")
                public.assert_not_called()
                start.assert_called_once()
                self.assertNotIn("--version", start.call_args.args[0])
                calls = [row["raw"] for row in case.agent_log() if row.get("dir") == "in"]
                self.assertEqual(sum(row.get("method") == "initialize" for row in calls), 1)
                self.assertTrue(any(row.get("method") == "session/new" for row in calls))

    def test_dsh_initialize_failure_remains_a_real_failure_in_both_seams(self):
        for operation in (self.execute, self.discover):
            with self.subTest(seam=operation.__name__), self.harness("dsh") as (case, module):
                case.extra_agent_args += ["--die-before-answer", "initialize"]
                with mock.patch.object(discovery, "probe") as public:
                    if operation == self.execute:
                        result = operation("dsh", case, module)
                        self.assertEqual((result.end.status, result.end.reason_code),
                                         ("error", "native-disconnected"))
                        self.assertFalse(result.model_started)
                    else:
                        with self.assertRaises(module.NativeError) as caught:
                            operation("dsh", case, module)
                        self.assertEqual(caught.exception.code, "native-disconnected")
                        self.assertIs(caught.exception.discovery_shutdown_confirmed, True)
                    public.assert_not_called()
                self.assertFalse(any(row.get("dir") == "in" and row["raw"].get("method") == "session/new"
                                     for row in case.agent_log()))
