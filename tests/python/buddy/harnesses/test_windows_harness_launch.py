"""Discover a generated native entry, select it, then speak each fake protocol."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest import mock

from buddy.harnesses.claude import test_native_run as claude_cases
from buddy.harnesses.codex import test_native_run as codex_cases
from buddy.harnesses.dsh import test_native_run as dsh_cases
from buddy.harnesses.zcode import test_native_run as zcode_cases
from buddy.harnesses.dsh.acp.support import FAKE_AGENT
from fake_executable import make_fake_executable
from hey_my_buddy.buddy.harnesses import discovery
from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.buddy.harnesses.runtime_selection import bound, command_for, launch_for
from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
from hey_my_buddy.buddy.roles.run_observers import FastCorrection
from hey_my_buddy.buddy.roles.structured_call import structured_prompt
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.protocol.owned_process import owned_popen


def _source_for(fixture: Path, harness: str, log: Path) -> str:
    """Supply only the fixture's missing CLI probe face before its protocol main."""
    return ("import runpy, sys\n"
            f"from pathlib import Path\n"
            "if sys.argv[1:] == ['--version']:\n"
            f"    print('{harness} 1.2.3')\n"
            "    raise SystemExit(0)\n"
            "if sys.argv[1:] == ['login', 'status']:\n"
            "    print('Logged in')\n"
            "    raise SystemExit(0)\n"
            "if sys.argv[1:] == ['auth', 'status', '--json']:\n"
            "    print('{\"loggedIn\":true,\"apiProvider\":\"firstParty\"}')\n"
            "    raise SystemExit(0)\n"
            "if sys.argv[1:2] == ['--capture-argv-stdin']:\n"
            f"    Path({str(log.parent / 'argv-capture.json')!r}).write_text("
            "__import__('json').dumps(sys.argv[2:], ensure_ascii=False), encoding='utf-8', newline='\\n')\n"
            f"    Path({str(log.parent / 'stdin-capture.bin')!r}).write_bytes(sys.stdin.buffer.read())\n"
            "    raise SystemExit(0)\n"
            + ("class _TeeInput:\n"
               "    def __init__(self, stream, destination):\n"
               "        self.stream = stream\n"
               "        self.destination = destination\n"
               "    def readline(self):\n"
               "        raw = self.stream.buffer.readline()\n"
               "        with open(self.destination, 'ab') as output:\n"
               "            output.write(raw)\n"
               "        return raw.decode('utf-8')\n"
               "    def __iter__(self):\n"
               "        return self\n"
               "    def __next__(self):\n"
               "        value = self.readline()\n"
               "        if not value:\n"
               "            raise StopIteration\n"
               "        return value\n"
               f"sys.stdin = _TeeInput(sys.stdin, {str(log.parent / 'stdin.bin')!r})\n"
               if harness == "claude" else "")
            + (f"sys.argv[1:1] = ['--log', {str(log)!r}, '--prompt-mode', 'final', "
               "'--final-answer', '{\"choice\":\"a\"}']\n" if harness == "dsh" else "")
            + f"sys.path.insert(0, {str(fixture.parent)!r})\n"
            + f"runpy.run_path({str(fixture)!r}, run_name='__main__')\n")


class HarnessLaunchChainTests(unittest.TestCase):
    def _case(self, case_type):
        case = case_type("runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        if case_type is not dsh_cases.NativeRunCase:
            original_cwd = case.cwd
            case.cwd = original_cwd.with_name("checkout (测试)&^%!")
            original_cwd.rename(case.cwd)
        return case

    def _select(self, harness: str, root: Path, fixture: Path, *, path: bool = False, electron: bool = False):
        entry_dir = root / "bin (测试)&^%!"
        source = _source_for(fixture, harness, root / "agent.log")
        if electron:
            source = ("import os, sys\n"
                      "assert os.environ['ELECTRON_RUN_AS_NODE'] == '1'\n"
                      "assert sys.argv[1] == '--fixture-internals'\n"
                      "del sys.argv[1:3]\n" + source)
        entry = make_fake_executable(entry_dir, harness, source=source)
        expected = [str(entry)]
        if electron:
            if entry.suffix != '.exe':
                executable = entry.with_suffix('.exe')
                entry.rename(executable)
            else:
                executable = entry
            archive = entry_dir / 'fixture.asar'
            archive.write_bytes(b'synthetic archive')
            target = archive / 'cli.js'
            expected = [str(executable), '--fixture-internals', str(target)]
            entry = entry_dir / 'entry.cmd'
            entry.write_text('set "ELECTRON_RUN_AS_NODE=1"\n'
                             f'"%~dp0{executable.name}" --fixture-internals "%~dp0fixture.asar\\cli.js" %*\n',
                             encoding='utf-8')
        environment = {**os.environ, "PATH": str(entry_dir) if path else ""}
        with mock.patch.object(discovery, "_home", return_value=root), \
             mock.patch.object(discovery, "_common_dirs", return_value=[]), \
             mock.patch.object(discovery, "_app_paths", return_value=[]), \
             mock.patch.object(discovery, "_registry_paths", return_value=[]):
            record = discovery.discover(harness, manual_path=None if path else str(entry),
                                        environment=environment)
        self.assertEqual(record["status"], "ready", record)
        self.assertEqual(record["command"], expected)
        self.assertEqual(record["source"], "path" if path else "manual")
        return record

    def test_claude_discovery_selection_and_stream_json_turn(self):
        case = self._case(claude_cases.WorkerCarrierTests)
        record = self._select("claude", case.base, claude_cases.FIXTURE)
        special = '& | < > ^ % " !\n\\ 😀'
        schema = {'type': 'object', 'description': special, 'properties': {'answer': {'type': 'string'}}}
        request = case.request(schema=schema).model_copy(update={
            'input_text': special})
        with bound([record]):
            self.assertEqual(command_for("claude"), record["command"])
            result = case.execute(request)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.parsed.value["outcome"]["disposition"], "completed")
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        argv = case.fixture_state()['argv']
        self.assertEqual(claude_cases.flag_value(argv, '--model'), request.buddy.model)
        self.assertEqual(json.loads(claude_cases.flag_value(argv, '--json-schema')), schema)
        raw = (case.base / 'stdin.bin').read_bytes()
        frames = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual(raw, b''.join((canonical_json(frame) + '\n').encode() for frame in frames))
        self.assertEqual([frame['message']['content'][0]['text'] for frame in frames
                          if frame.get('type') == 'user'], [special])
        sentinel = case.base / 'shell-sentinel'
        edge_args = ['& echo injected > ' + str(sentinel), '|', '<', '>', '^', '%', '"', '!',
                     'line 1\nline 2', '', 'tail\\', '😀']
        payload = b'bytes: \x00\n' + special.encode('utf-8') + b'\n'
        process = owned_popen([record['command'][0], '--capture-argv-stdin', *edge_args],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, start_new_session=True)
        handle = ProcessHandle(process, own_group=True, log_paths={})
        try:
            output, errors = process.communicate(payload, timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if not handle.shutdown_confirmed(settle_seconds=0.5):
                handle.terminate(grace_seconds=1)
            self.assertTrue(handle.shutdown_confirmed(settle_seconds=0.5))
        self.assertEqual(process.returncode, 0, errors.decode('utf-8', 'replace'))
        self.assertEqual(output, b'')
        self.assertEqual(json.loads((case.base / 'argv-capture.json').read_text(encoding="utf-8")), edge_args)
        self.assertEqual((case.base / 'stdin-capture.bin').read_bytes(), payload)
        self.assertFalse(sentinel.exists())

    def test_codex_path_discovery_selection_and_app_server_turn(self):
        case = self._case(codex_cases.SeamCase)
        os.environ.pop("HMB_CODEX_CLI", None)
        os.environ["HMB_CODEX_FIXTURE_STATE"] = str(case.base / "codex-state.json")
        os.environ["HMB_CODEX_FIXTURE_CASE"] = "ok"
        record = self._select("codex", case.base, codex_cases.FIXTURE, path=True)
        request = case.request(permission="none", output_schema=codex_cases.FAST_SCHEMA,
                               prompt="Pick a buddy")
        correction = FastCorrection(codex_cases.FAST_SCHEMA, "Pick a buddy")
        with bound([record]):
            self.assertEqual(command_for("codex"), record["command"])
            result = codex_cases.run(request, observer=correction.observer,
                                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", repr(result))
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_zcode_discovery_selection_and_app_server_turn(self):
        case = self._case(zcode_cases.NativeRunCase)
        record = self._select("zcode", case.base, case.fake)
        provider = Path(record["command"][-1]).parent / "provider/zcode-builtin.json"
        provider.parent.mkdir()
        shutil.copyfile(case.root / "provider/zcode-builtin.json", provider)
        with bound([record], environment=case.environment):
            self.assertEqual(command_for("zcode"), record["command"])
            prompt = structured_prompt("Pick a buddy", zcode_cases.SCHEMA)
            result = zcode_cases.run(case.fast_request(prompt),
                                     observer=FastCorrection(zcode_cases.SCHEMA, prompt).observer,
                                     services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", repr(result) + "\n" + "\n".join(
            Path(ref.location).read_text(encoding="utf-8") for ref in result.evidence_refs if ref.kind == "native-stderr"))
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_dsh_discovery_selection_and_acp_turn(self):
        case = self._case(dsh_cases.NativeRunCase)
        record = self._select("dsh", case.base, FAKE_AGENT, electron=True)
        request = case.fast_request("Pick a buddy")
        cwd = Path(request.cwd).with_name("checkout (测试)&^%!")
        Path(request.cwd).rename(cwd)
        request = request.model_copy(update={"cwd": str(cwd)})
        with bound([record]), mock.patch.object(dsh_cases.native_run, "launch_for", launch_for):
            self.assertEqual(command_for("dsh"), record["command"])
            result = dsh_cases.run(request,
                                   observer=FastCorrection(dsh_cases.SCHEMA, "Pick a buddy").observer,
                                   services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", repr(result))
        self.assertTrue(result.completion_evidence.stream_end)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")


if __name__ == "__main__":
    unittest.main()
