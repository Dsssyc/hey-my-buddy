"""Synthetic static launch descriptions; no vendor shim text or real CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from fake_executable import make_fake_executable
from hey_my_buddy.buddy.harnesses import discovery, runtime_selection
from hey_my_buddy.buddy.harnesses.dsh.acp import launch
from hey_my_buddy.buddy.harnesses.dsh import native_run
from hey_my_buddy.buddy.harnesses.claude import config as claude
from hey_my_buddy.buddy.harnesses.codex import config as codex


class LiteralShimTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory).resolve()
        self.bin = self.root / 'runtime/bin'
        self.bin.mkdir(parents=True)
        self.program = self.root / 'Engine fixture.exe'
        self.program.write_bytes(b'fixture')
        self.archive = self.root / 'payload.asar'
        self.archive.write_bytes(b'synthetic archive placeholder')
        self.shim = self.bin / 'entry.cmd'
        self.content = ('set "ELECTRON_RUN_AS_NODE=1"\n'
                        '"%~dp0..\\..\\Engine fixture.exe" --inspect-fixture '
                        '"%~dp0..\\..\\payload.asar\\entry.js" %*\n')
        self.shim.write_text(self.content, encoding='utf-8')

    def candidate(self):
        return {'path': str(self.shim), 'source': 'manual', 'exists': True,
                'fingerprint': discovery.file_fingerprint(self.shim)}

    def record(self, harness='dsh', command=None):
        return {'harness': harness, 'status': 'ready', 'executable': str(self.shim),
                'command': command or [str(self.program), '--inspect-fixture', str(self.archive / 'entry.js')]}

    def test_relative_program_literal_arguments_environment_and_archive(self):
        with mock.patch.object(discovery, 'os', SimpleNamespace(name='nt')),\
             mock.patch.object(discovery, '_node_for') as node:
            command = discovery._launch_command(self.shim, {})
        self.assertEqual(command, self.record()['command'])
        self.assertEqual(discovery.shim_environment(self.shim), {'ELECTRON_RUN_AS_NODE': '1'})
        node.assert_not_called()
        self.assertFalse((self.archive / 'entry.js').is_file())

    def test_node_selection_uses_the_same_description_with_literal_environment(self):
        target = self.bin / 'entry.js'
        target.write_text('synthetic entry', encoding='utf-8')
        self.shim.write_text('set "LITERAL_FLAG=some  value"\nset "MODE=node"\n'
                             'set "runner=node.exe"\n'
                             '"%runner%" --literal-option "%dp0%\\entry.js" %*\n', encoding='utf-8')
        with mock.patch.object(discovery, '_node_for', return_value=('node.exe', 'v1.2.3')):
            self.assertEqual(discovery._launch_command(self.shim, {}),
                             ['node.exe', '--literal-option', str(target)])
        self.assertEqual(discovery.shim_environment(self.shim), {'LITERAL_FLAG': 'some  value', 'MODE': 'node'})

    def test_node_selection_scaffolding_is_not_interpreted_as_child_environment(self):
        (self.bin / 'entry.js').write_text('fixture', encoding='utf-8')
        self.shim.write_text('set dp0=%~dp0\n'
                             'if exist "%dp0%\\node.exe" (\n'
                             'set "runner=%dp0%\\node.exe"\n'
                             ') else (\nset "runner=node"\n)\n'
                             '"%runner%" "%dp0%\\entry.js" %*\n', encoding='utf-8')
        parsed = discovery._script_launch(self.shim)
        self.assertIsNone(parsed.program)
        self.assertEqual(parsed.environment, {})

    def test_node_directory_helper_is_recognized_in_either_position(self):
        (self.bin / 'entry.js').write_text('fixture', encoding='utf-8')
        helper = ':find_dp0\nset dp0=%~dp0\nexit /b\n'
        body = ('call :find_dp0\nset "runner=node"\n'
                '"%runner%" "%dp0%\\entry.js" %*\n')
        for content in ('goto start\n' + helper + ':start\n' + body, body + helper):
            with self.subTest(content=content):
                self.shim.write_text(content, encoding='utf-8')
                parsed = discovery._script_launch(self.shim)
                self.assertIsNone(parsed.program)
                self.assertEqual(parsed.environment, {})

    def test_unsupported_descriptions_never_spawn(self):
        rejected = {
            'unquoted program': self.content.replace('"%~dp0..\\..\\Engine fixture.exe"', '%~dp0..\\..\\Engine.exe'),
            'absolute program': self.content.replace('%~dp0..\\..\\Engine fixture.exe', 'C:\\apps\\Engine.exe'),
            'absolute target': self.content.replace('%~dp0..\\..\\payload.asar\\entry.js', 'C:\\apps\\entry.js'),
            'two targets': self.content.replace(' %*', ' "%~dp0..\\..\\payload.asar\\other.js" %*'),
            'expanded value': self.content.replace('=1"', '=%USERPROFILE%"'),
            'delayed value': self.content.replace('=1"', '=!FLAG!"'),
            'conditional assignment': self.content.replace('set "', 'if defined FLAG set "'),
            'missing program': self.content.replace('Engine fixture.exe', 'missing.exe'),
            'missing archive': self.content.replace('payload.asar', 'missing.asar'),
            'missing ordinary entry': self.content.replace('payload.asar\\entry.js', 'missing.js'),
            'unquoted target': self.content.replace('"%~dp0..\\..\\payload.asar\\entry.js"', '%~dp0..\\..\\payload.asar\\entry.js'),
            'two calls': self.content + self.content.splitlines()[-1] + '\n',
            'shell suffix': self.content.replace(' %*', ' %* & echo injected'),
            'computed argument': self.content.replace('--inspect-fixture', '--inspect=%FLAG%'),
            'unknown command': 'echo changed\n' + self.content,
            'skipped assignment': 'goto start\nset "SKIPPED=1"\n:start\n' + self.content,
            'early exit': 'exit /b\n' + self.content,
            'assignment after launch': self.content + 'set "OTHER_FLAG=1"\n',
            'non JS entry': self.content.replace('entry.js', 'entry.txt'),
            'selector after launch': '"%runner%" "%~dp0..\\..\\payload.asar\\entry.js" %*\nset "runner=node"\n',
        }
        for label, content in rejected.items():
            with self.subTest(label=label):
                self.shim.write_text(content, encoding='utf-8')
                with mock.patch.object(discovery, 'probe') as probe,\
                     mock.patch.object(discovery, '_probe_dsh_version') as dsh_probe:
                    result = discovery._candidate_handshake('dsh', self.candidate(), {}, time.monotonic() + 5)
                self.assertEqual(result['reason']['code'], 'harness.entry-unsupported')
                probe.assert_not_called()
                dsh_probe.assert_not_called()

    def test_version_probe_gets_literal_environment_without_inheriting_it(self):
        source = {'ELECTRON_RUN_AS_NODE': 'inherited-value', 'SECRET': 'excluded', 'PATH': ''}
        with mock.patch.object(discovery, '_native_command', side_effect=lambda p: [str(p)]),\
             mock.patch.object(discovery, 'probe', return_value=(None, b'fixture 1.2.3')) as probe:
            result = discovery._candidate_handshake('zcode', self.candidate(), source, time.monotonic() + 5)
        self.assertEqual(result['status'], 'ready')
        environment = probe.call_args.args[1]
        self.assertEqual(environment['ELECTRON_RUN_AS_NODE'], '1')
        self.assertNotIn('SECRET', environment)
        self.assertEqual(source['ELECTRON_RUN_AS_NODE'], 'inherited-value')

    def test_selection_file_and_all_runtime_environments_derive_the_same_values(self):
        records = {name: self.record(name) for name in ('dsh', 'claude', 'codex', 'zcode')}
        selection = self.root / 'selection.json'
        selection.write_text(json.dumps(records), encoding='utf-8')
        source = {runtime_selection.RECORD_FILE: str(selection), 'PATH': ''}
        for harness in records:
            with self.subTest(harness=harness):
                result = runtime_selection.native_environment(source, harness=harness)
                self.assertEqual(result['ELECTRON_RUN_AS_NODE'], '1')
                self.assertNotIn(runtime_selection.RECORD_FILE, result)
        with runtime_selection.bound(list(records.values()), environment=source):
            self.assertEqual(claude.native_environment(source)['ELECTRON_RUN_AS_NODE'], '1')
            self.assertEqual(codex.native_environment(source)['ELECTRON_RUN_AS_NODE'], '1')
            controller = runtime_selection.controller_environment(self.root, source)
        saved = json.loads(Path(controller[runtime_selection.RECORD_FILE]).read_text(encoding='utf-8'))
        self.assertEqual(saved, records)  # no new stored field

    def test_development_overrides_share_the_parser_and_environment_for_all_harnesses(self):
        with mock.patch.dict(os.environ, {'HMB_DEV_SOURCE': '1'}), \
             mock.patch.object(discovery, '_native_command', side_effect=lambda path: [str(path)]), \
             mock.patch.object(discovery, 'discover') as discover:
            for name in ('claude', 'codex', 'zcode', 'dsh'):
                source = {f'HMB_{name.upper()}_CLI': str(self.shim), 'ELECTRON_RUN_AS_NODE': 'parent'}
                with self.subTest(harness=name):
                    command, environment = runtime_selection.launch_for(name, source)
                    self.assertEqual(command, self.record()['command'])
                    self.assertEqual(environment, {'ELECTRON_RUN_AS_NODE': '1'})
                    child = runtime_selection.native_environment(source, harness=name)
                    self.assertEqual(child['ELECTRON_RUN_AS_NODE'], '1')
                    self.assertNotIn(f'HMB_{name.upper()}_CLI', child)
            discover.assert_not_called()

    def test_dsh_runtime_forwards_literal_environment_to_the_acp_launcher(self):
        root = self.root / 'turn'
        root.mkdir()
        with runtime_selection.bound([self.record()]),\
             mock.patch.object(native_run.AcpClient, 'start') as start:
            native_run._launch_agent(cwd=str(root), launch_root=root, invocation_root=root,
                                     sessions_root=root / 'sessions', scope_rows=[],
                                     extra_env={'DSH_PERMISSION_MODE': 'read-only'}, permission_policy=None)
        self.assertEqual(start.call_args.args[0][:3], self.record()['command'])
        self.assertEqual(start.call_args.kwargs['launch_environment'], {'ELECTRON_RUN_AS_NODE': '1'})
        self.assertEqual(start.call_args.kwargs['extra_env'], {'DSH_PERMISSION_MODE': 'read-only'})

    def test_dsh_probe_and_launch_keep_literal_environment_in_the_real_child(self):
        capture = self.root / 'child-environment.json'
        entry = make_fake_executable(self.bin, 'engine-fixture', source=
            'import json, os\nfrom pathlib import Path\n'
            f'Path({str(capture)!r}).write_text(json.dumps(dict(os.environ)), encoding="utf-8")\n'
            'print("fixture 1.2.3")\n')
        if entry.suffix != '.exe':
            renamed = entry.with_suffix('.exe')
            entry.rename(renamed)
            entry = renamed
        self.shim.write_text('set "ELECTRON_RUN_AS_NODE=1"\n'
                             f'"%~dp0{entry.name}" "%~dp0..\\..\\payload.asar\\entry.js" %*\n', encoding='utf-8')
        result = discovery._candidate_handshake('dsh', self.candidate(), {'PATH': ''}, time.monotonic() + 10)
        self.assertEqual(result['status'], 'ready', result)
        self.assertEqual(json.loads(capture.read_text())['ELECTRON_RUN_AS_NODE'], '1')
        capture.unlink()
        root = self.root / 'launch'
        root.mkdir()
        with runtime_selection.bound([self.record(command=result['command'])]):
            command, extra = runtime_selection.launch_for('dsh', {})
        process, handle = launch(command, private_root=root, source_environment={'PATH': ''}, launch_environment=extra)
        process.communicate(timeout=10)
        self.assertTrue(handle.shutdown_confirmed(settle_seconds=0.2))
        self.assertEqual(json.loads(capture.read_text())['ELECTRON_RUN_AS_NODE'], '1')


if __name__ == '__main__':
    unittest.main()
