"""Development overrides resolve exactly one entry through discovery's boundary."""
from __future__ import annotations

import os
from pathlib import Path
from temporary import TemporaryDirectory
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import discovery, runtime_selection
from hey_my_buddy.buddy.harnesses.claude import config as claude
from hey_my_buddy.buddy.harnesses.codex import config as codex
from hey_my_buddy.buddy.harnesses.zcode import config as zcode


class ExplicitEntryTests(unittest.TestCase):
    def test_shared_launch_passes_only_its_development_fixture_values(self):
        with mock.patch.dict(os.environ, {"HMB_DEV_SOURCE": "1"}), mock.patch.object(
                runtime_selection, "launch_with_environment", side_effect=lambda *_args: (["native"], {})):
            for name, config in (("claude", claude), ("codex", codex)):
                with self.subTest(harness=name):
                    own = f"HMB_{name.upper()}_FIXTURE_"
                    other = "HMB_CODEX_FIXTURE_CASE" if name == "claude" else "HMB_CLAUDE_FIXTURE_CASE"
                    command, child = config.launch({own + "CASE": "ok", own + "STATE": "/fixture/state",
                                                    other: "other"})
                    self.assertEqual(command, ["native"])
                    self.assertEqual(child, {own + "CASE": "ok", own + "STATE": "/fixture/state"})

    def test_each_override_uses_the_shared_resolver_and_preserves_refusal(self):
        with TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {"HMB_DEV_SOURCE": "1"}):
            entry = Path(temporary) / "native-entry.exe"
            for name, config, exception in (("claude", claude, claude.ClaudeUnavailable),
                                            ("codex", codex, codex.CodexUnavailable),
                                            ("zcode", zcode, zcode.NativeError),
                                            ("dsh", None, discovery.EntryUnavailable)):
                environment = {f"HMB_{name.upper()}_CLI": str(entry)}
                command_for = config.cli_command if config is not None else lambda env: runtime_selection.launch_for("dsh", env)[0]
                with self.subTest(harness=name), mock.patch.object(discovery, "_launch_command", return_value=["node.exe", "cli.js"]) as resolve:
                    self.assertEqual(command_for(environment), ["node.exe", "cli.js"])
                    resolve.assert_called_once_with(entry, environment)
                with self.subTest(harness=name), mock.patch.object(discovery, "_launch_command", side_effect=discovery.EntryUnsupported()):
                    with self.assertRaises(exception) as caught:
                        command_for(environment)
                    self.assertEqual(caught.exception.reason["code"], "harness.entry-unsupported")

    def test_no_override_uses_the_selected_command(self):
        with mock.patch.dict(os.environ, {"HMB_DEV_SOURCE": "1"}), mock.patch(
                "hey_my_buddy.buddy.harnesses.runtime_selection.launch_for", return_value=(["selected.exe"], {})) as selected:
            for name, config in (("claude", claude), ("codex", codex), ("zcode", zcode)):
                self.assertEqual(config.cli_command({}), ["selected.exe"])
                selected.assert_called_with(name, {})

    def test_overrides_require_development_mode_for_all_four_harnesses(self):
        records = [{'harness': name, 'status': 'ready', 'command': ['selected.exe']}
                   for name in ('claude', 'codex', 'zcode', 'dsh')]
        environment = {f'HMB_{record["harness"].upper()}_CLI': 'override.exe' for record in records}
        with mock.patch.dict(os.environ, {'HMB_DEV_SOURCE': '0'}), runtime_selection.bound(records), \
             mock.patch.object(discovery, '_launch_command') as resolve:
            for record in records:
                with self.subTest(harness=record['harness']):
                    self.assertEqual(runtime_selection.launch_for(record['harness'], environment),
                                     (['selected.exe'], {}))
            resolve.assert_not_called()

    def test_development_controller_preserves_all_four_overrides(self):
        with TemporaryDirectory() as temporary:
            source = {'HMB_DEV_SOURCE': '1', **{f'HMB_{name.upper()}_CLI': f'{name}.exe'
                       for name in ('claude', 'codex', 'zcode', 'dsh')}}
            result = runtime_selection.controller_environment(temporary, source)
            for key, value in source.items():
                self.assertEqual(result[key], value)

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
