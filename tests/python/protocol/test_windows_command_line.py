"""The final Windows CreateProcess command line is checked before spawn intent."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from hey_my_buddy.buddy.harnesses.claude.config import execution_args
from hey_my_buddy.buddy.harnesses.claude import native_run as claude_run
from hey_my_buddy.buddy.harnesses.claude.protocol import ClaudeProtocolError
from hey_my_buddy.buddy.harnesses.codex import native_run as codex_run
from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError
from hey_my_buddy.buddy.harnesses.dsh import native_run as dsh_run
from hey_my_buddy.buddy.harnesses.dsh.protocol import NativeError as DshNativeError
from hey_my_buddy.buddy.harnesses.zcode import native_run as zcode_run
from hey_my_buddy.buddy.harnesses.zcode.protocol import NativeError as ZcodeNativeError
from hey_my_buddy.buddy.harnesses import discovery
from hey_my_buddy.buddy.harnesses.run_contract import MAX_SCHEMA_BYTES
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.protocol import owned_process, process_identity


class WindowsCommandLineTests(unittest.TestCase):
    def test_boundary_counts_terminating_nul(self):
        owned_process.check_windows_command_line(["x" * 32766], windows=True)
        with self.assertRaises(owned_process.WindowsCommandLineTooLong) as caught:
            owned_process.check_windows_command_line(["x" * 32767], windows=True)
        self.assertEqual(caught.exception.utf16_units, 32768)
        self.assertEqual(caught.exception.limit, 32767)

    def test_non_bmp_and_windows_quoting_use_final_encoded_argv(self):
        for argv in (["tool", "😀" * 16381], ["tool", 'a "quoted" value\\']):
            expected = len(subprocess.list2cmdline(argv).encode("utf-16-le")) // 2 + 1
            with self.subTest(argv=argv[:1]):
                if expected > owned_process.WINDOWS_COMMAND_LINE_LIMIT:
                    with self.assertRaises(owned_process.WindowsCommandLineTooLong) as caught:
                        owned_process.check_windows_command_line(argv, windows=True)
                    self.assertEqual(caught.exception.utf16_units, expected)
                else:
                    owned_process.check_windows_command_line(argv, windows=True)
        self.assertGreater(len(subprocess.list2cmdline(["tool", 'a "quoted" value\\'])),
                           len("tool") + len('a "quoted" value\\'))

    def test_overflow_neither_creates_a_process_nor_registers_intent(self):
        with mock.patch.object(process_identity, "begin_native_process_spawn") as begin, \
             mock.patch.object(owned_process.subprocess, "Popen") as popen:
            with self.assertRaises(owned_process.WindowsCommandLineTooLong) as caught:
                owned_process.owned_popen(["tool", "x" * 32767], windows_api=object())
        begin.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(caught.exception.owned_popen_evidence,
                         owned_process.ProcessCreationEvidence(False, True, None))
        self.assertEqual(caught.exception.code, "harness.command-line-too-long")
        self.assertNotIn("x" * 100, str(caught.exception))

    def test_legal_claude_schema_can_exceed_windows_argv_limit(self):
        schema = {"type": "object", "properties": {"answer": {"const": "x" * 33000}}}
        self.assertLess(len(canonical_json(schema).encode("utf-8")), MAX_SCHEMA_BYTES)
        argv = ["claude.exe", *execution_args(session_id="00000000-0000-0000-0000-000000000001",
                                               model="sample", effort="default", settings_path="C:\\settings.json",
                                               read_only=False, output_schema=schema)]
        with self.assertRaises(owned_process.WindowsCommandLineTooLong) as caught:
            owned_process.check_windows_command_line(argv, windows=True)
        self.assertGreater(caught.exception.utf16_units, caught.exception.limit)

    def test_oversize_schema_reaches_shared_boundary_without_truncation(self):
        schema = {"type": "object", "properties": {"answer": {"const": "x" * 33000}}}
        args = execution_args(session_id="00000000-0000-0000-0000-000000000001",
                              model="sample", effort="default", settings_path="settings.json",
                              read_only=False, output_schema=schema)
        original = owned_process.check_windows_command_line
        owned = []
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(claude_run, "probe_version", return_value="fixture"), \
             mock.patch.object(owned_process, "check_windows_command_line", side_effect=lambda argv, **_: original(argv, windows=True)), \
             mock.patch.object(process_identity, "begin_native_process_spawn") as begin, \
             mock.patch.object(owned_process.subprocess, "Popen") as popen:
            with self.assertRaises(ClaudeProtocolError) as caught:
                claude_run._spawn_native(command=["claude.exe"], args=args, cwd=directory,
                                         environment={}, invocation_root=Path(directory), deadline=1,
                                         cancel=None, owned=owned)
        self.assertEqual(caught.exception.code, "harness.command-line-too-long")
        self.assertEqual(args[args.index("--json-schema") + 1], canonical_json(schema))
        begin.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(owned, [{"created": False, "shutdown": True, "exit_code": None}])

    def test_probe_keeps_length_counts_and_no_child_receipt(self):
        argv = ["node.exe", "cli.js", "x" * 32767]
        original = owned_process.check_windows_command_line
        receipt = []
        with mock.patch.object(process_identity, "native_process_journal_active", return_value=False), \
             mock.patch.object(owned_process, "check_windows_command_line", side_effect=lambda command: original(command, windows=True)), \
             mock.patch.object(discovery.subprocess, "Popen") as popen:
            reason, output = discovery.probe(argv, {}, owned=receipt)
        self.assertEqual(reason["code"], "harness.command-line-too-long")
        self.assertEqual(reason["params"], {"operation": "probe", "limit": 32767,
            "utf16Units": len(subprocess.list2cmdline(argv).encode("utf-16-le")) // 2 + 1})
        self.assertEqual(output, b"")
        self.assertEqual(receipt, [{"created": False, "shutdown": True, "exit_code": None}])
        popen.assert_not_called()

    def test_claude_and_codex_keep_length_reason_and_no_child_receipt(self):
        overflow = owned_process.WindowsCommandLineTooLong(32768)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude_owned = []
            with mock.patch.object(claude_run, "probe_version", return_value="fixture"), \
                 mock.patch.object(claude_run, "owned_popen", side_effect=overflow):
                with self.assertRaises(ClaudeProtocolError) as caught:
                    claude_run._spawn_native(command=["claude.exe"], args=[], cwd=directory,
                                             environment={}, invocation_root=root, deadline=1,
                                             cancel=None, owned=claude_owned)
            self.assertEqual(caught.exception.code, overflow.code)
            self.assertEqual(claude_owned, [{"created": False, "shutdown": True, "exit_code": None}])

            codex_owned = []
            prep = SimpleNamespace(stderr_path=root / "codex-stderr", command=["codex.exe"],
                                   cwd=directory, environment={})
            with mock.patch.object(codex_run, "owned_popen", side_effect=overflow):
                with self.assertRaises(CodexProtocolError) as caught:
                    codex_run._spawn_app_server(prep, None, codex_owned)
            self.assertEqual(caught.exception.code, overflow.code)
            self.assertEqual(codex_owned, [{"created": False, "shutdown": True, "exit_code": None}])

    def test_dsh_keeps_length_reason_and_no_child_receipt(self):
        overflow = owned_process.WindowsCommandLineTooLong(32768)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(dsh_run, "launch_for", return_value=(["dsh.exe"], {})), \
                 mock.patch.object(dsh_run.AcpClient, "start", side_effect=overflow):
                with self.assertRaises(DshNativeError) as caught:
                    dsh_run._launch_agent(cwd=directory, launch_root=root, invocation_root=root,
                                          sessions_root=root / "sessions", scope_rows=[], extra_env={},
                                          permission_policy=None)
        self.assertEqual(caught.exception.code, overflow.code)
        self.assertEqual(caught.exception.spawn_evidence,
                         {"phase": "pre-spawn-validation", "created": False,
                          "groupObserved": "gone", "shutdownConfirmed": True})

    def test_zcode_keeps_length_reason_and_no_child_receipt(self):
        overflow = owned_process.WindowsCommandLineTooLong(32768)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owned = []
            with mock.patch.object(zcode_run, "environment_for", return_value={}), \
                 mock.patch.object(zcode_run, "cli_command", return_value=["zcode.exe"]), \
                 mock.patch.object(discovery, "native_environment", return_value={}), \
                 mock.patch.object(zcode_run, "provider_paths", return_value=(root / "builtin.json", None)), \
                 mock.patch.object(zcode_run, "load_runtime_preferences", return_value={}), \
                 mock.patch.object(zcode_run, "probe_version", return_value="fixture"), \
                 mock.patch.object(zcode_run, "owned_popen", side_effect=overflow):
                with self.assertRaises(ZcodeNativeError) as caught:
                    zcode_run._spawn_app_server(cwd=directory, invocation_root=root, native_root=root,
                                                deadline=1, cancel=None, stderr_path=root / "zcode-stderr",
                                                no_tools=False, owned=owned)
        self.assertEqual(caught.exception.code, overflow.code)
        self.assertEqual(owned, [{"created": False, "shutdown": True, "exit_code": None}])


if __name__ == "__main__":
    unittest.main()
