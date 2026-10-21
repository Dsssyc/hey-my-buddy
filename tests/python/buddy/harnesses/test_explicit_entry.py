"""Development overrides resolve exactly one entry through discovery's boundary."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import discovery
from hey_my_buddy.buddy.harnesses.claude import config as claude
from hey_my_buddy.buddy.harnesses.codex import config as codex
from hey_my_buddy.buddy.harnesses.zcode import config as zcode


class ExplicitEntryTests(unittest.TestCase):
    def test_each_override_uses_the_shared_resolver_and_preserves_refusal(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {"HMB_DEV_SOURCE": "1"}):
            entry = Path(temporary) / "npm-entry.cmd"
            for name, config, exception in (("claude", claude, claude.ClaudeUnavailable),
                                            ("codex", codex, codex.CodexUnavailable),
                                            ("zcode", zcode, zcode.NativeError)):
                environment = {f"HMB_{name.upper()}_CLI": str(entry)}
                with self.subTest(harness=name), mock.patch.object(discovery, "_launch_command", return_value=["node.exe", "cli.js"]) as resolve:
                    self.assertEqual(config.cli_command(environment), ["node.exe", "cli.js"])
                    resolve.assert_called_once_with(entry, environment)
                with self.subTest(harness=name), mock.patch.object(discovery, "_launch_command", side_effect=discovery.EntryUnsupported()):
                    with self.assertRaises(exception) as caught:
                        config.cli_command(environment)
                    self.assertEqual(caught.exception.reason["code"], "harness.entry-unsupported")

    def test_no_override_uses_the_selected_command(self):
        with mock.patch.dict(os.environ, {"HMB_DEV_SOURCE": "1"}), mock.patch(
                "hey_my_buddy.buddy.harnesses.runtime_selection.command_for", return_value=["selected.exe"]) as selected:
            for name, config in (("claude", claude), ("codex", codex), ("zcode", zcode)):
                self.assertEqual(config.cli_command({}), ["selected.exe"])
                selected.assert_called_with(name, {})

    def test_unconfirmed_node_probe_is_not_reported_as_no_child(self):
        from buddy.harnesses.claude import test_native_run as claude_cases
        from buddy.harnesses.codex import test_native_run as codex_cases
        from buddy.harnesses.zcode import test_native_run as zcode_cases
        from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE

        for name, case_type, request_for, run in (
                ('claude', claude_cases.NativeRunCase, lambda case: case.request(), claude_cases.run),
                ('codex', codex_cases.SeamCase, lambda case: case.request(permission='none',
                    output_schema=codex_cases.FAST_SCHEMA, prompt='fixture'), codex_cases.run),
                ('zcode', zcode_cases.NativeRunCase, lambda case: case.fast_request('fixture'), zcode_cases.run)):
            with self.subTest(harness=name):
                case = case_type('runTest')
                case.setUp()
                try:
                    with mock.patch.dict(os.environ, {f'HMB_{name.upper()}_CLI': 'fixture.cmd'}), \
                         mock.patch.object(discovery, '_launch_command',
                                           side_effect=discovery.EntryUnavailable('shutdown-unverified')):
                        result = run(request_for(case), observer=lambda _facts: FEEDBACK_CONTINUE,
                                     services=None, cancelled=lambda: False)
                    self.assertEqual(result.end.reason_code, 'harness.shutdown-unverified')
                    self.assertEqual(result.stop_evidence.native.group_state, 'unknown')
                finally:
                    case.doCleanups()
