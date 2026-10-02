"""One-shot native probes are inert before approval and judge real tool facts."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from buddy.adapters.base import AdapterOutcome
from fixtures.router_tool_receipt import tool_receipt

path = Path(__file__).resolve().parents[1] / 'probes' / 'router_readonly_tools.py'
spec = importlib.util.spec_from_file_location('readonly_tools_probe', path)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ReadonlyToolsProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='private-readonly-probe-')
        self.addCleanup(self.temp.cleanup)
        self.args = SimpleNamespace(adapter='dsh', provider='deepseek-official', model='deepseek-flash',
                                    effort='off', output_root=Path(self.temp.name) / 'probe', execute=False)

    def prepared(self):
        report = probe.run(self.args)
        self.args.expected_packet_sha256 = report['packetSha256']
        return json.loads(Path(report['packet']).read_text())

    def outcome(self, packet):
        result = {**tool_receipt(packet['binding'], 1), 'resolved': packet['configuration'],
                  'usage': {'toolCalls': 1, 'bytesRead': None}, 'correctionCount': 0,
                  'processState': {'shutdownConfirmed': True},
                  'rawAnswer': {'profileId': packet['expected']['profileId'],
                                'reason': packet['expected']['marker'], 'evidence': []}}
        return AdapterOutcome(status='ok', result=result, shutdown_confirmed=True)

    def test_preparation_never_selects_or_starts_a_native_adapter(self):
        with patch.object(probe, 'adapter_for', side_effect=AssertionError('no native call')), \
             patch('subprocess.Popen', side_effect=AssertionError('no process')):
            report = probe.run(self.args)
            self.assertEqual(report['modelCalls'], 0)
            self.assertEqual(probe.run(self.args)['packetSha256'], report['packetSha256'])
        packet = json.loads(Path(report['packet']).read_text())
        self.assertNotIn(packet['expected']['marker'], packet['request']['prompt'])
        self.assertEqual(packet['request']['budget']['toolCalls'], 8)

    def test_only_one_execution_is_possible_for_the_prepared_packet(self):
        packet = self.prepared()
        native, handle = Mock(), Mock()
        native.start_read_only_structured.return_value = handle
        handle.wait.return_value = 0
        handle.shutdown_confirmed.return_value = True
        self.args.execute = True
        with patch.object(probe, 'adapter_for', return_value=native), \
             patch.object(probe.read_only, 'collect', return_value=self.outcome(packet)):
            self.assertEqual(probe.run(self.args)['status'], 'passed')
            with self.assertRaises(FileExistsError):
                probe.run(self.args)
        native.start_read_only_structured.assert_called_once()
        context, request = native.start_read_only_structured.call_args.args
        self.assertIsNone(context.agent_credential)
        self.assertIsNone(context.turn)
        self.assertEqual(request.budget['timeoutSeconds'], 60)
        self.assertTrue(Path(context.environment['BUDDY_STATE_DIR']).parent.name.startswith('buddy-checks-'))
        self.assertEqual(Path(context.environment['BUDDY_RUNTIME_ROOT']).parent,
                         Path(context.environment['BUDDY_STATE_DIR']).parent)
        self.assertFalse(Path(context.environment['BUDDY_STATE_DIR']).parent.exists())

    def test_changed_input_or_configuration_cannot_reuse_approval(self):
        self.prepared()
        (self.args.output_root / 'frozen' / 'selection-guide.txt').write_text('changed')
        self.args.execute = True
        with patch.object(probe, 'adapter_for', side_effect=AssertionError('no native call')):
            with self.assertRaises(ValueError):
                probe.run(self.args)

    def test_changed_request_cannot_reuse_the_prepared_approval_digest(self):
        self.prepared()
        path = self.args.output_root / 'prepared.json'
        changed = json.loads(path.read_text())
        changed['request']['prompt'] = 'a different request'
        path.write_text(json.dumps(changed))
        self.args.execute = True
        with patch.object(probe, 'adapter_for', side_effect=AssertionError('no native call')):
            with self.assertRaisesRegex(ValueError, 'exact prepared packet digest'):
                probe.run(self.args)

    def test_a_final_answer_cannot_replace_tool_identity_budget_or_stop_proof(self):
        packet = self.prepared()
        good = self.outcome(packet)
        self.assertEqual(probe.evaluate(packet, good, elapsed_ms=100, unchanged=True)['status'], 'passed')
        mutations = (
            lambda out: out.result.pop('toolEvidence'),
            lambda out: out.result['toolEvidence']['binding'].update(attemptId='foreign'),
            lambda out: out.result['toolEvidence'].update(streamComplete=False),
            lambda out: out.result['usage'].update(toolCalls=0),
            lambda out: out.result.update(correctionCount=2),
            lambda out: out.result['rawAnswer'].update(reason='guessed'),
            lambda out: out.result['processState'].update(shutdownConfirmed=False),
        )
        for mutate in mutations:
            bad = copy.deepcopy(good)
            mutate(bad)
            self.assertEqual(probe.evaluate(packet, bad, elapsed_ms=100, unchanged=True)['status'], 'failed')
        self.assertEqual(probe.evaluate(packet, good, elapsed_ms=60001, unchanged=True)['status'], 'failed')
        self.assertEqual(probe.evaluate(packet, good, elapsed_ms=100, unchanged=False)['status'], 'failed')

    def test_all_harness_packets_freeze_policy_without_selecting_a_native_adapter(self):
        for adapter in probe.ADAPTERS:
            with self.subTest(adapter=adapter), patch.object(probe, 'adapter_for', side_effect=AssertionError('no native call')), \
                 patch('subprocess.Popen', side_effect=AssertionError('no process')):
                self.args.adapter = adapter
                self.args.output_root = Path(self.temp.name) / adapter
                packet = self.prepared()
                self.assertIs(packet['systemSandbox'], probe.system_sandbox(adapter))

    def test_native_command_reading_requires_the_frozen_system_sandbox_class(self):
        for adapter in ('codex', 'dsh'):
            with self.subTest(adapter=adapter):
                self.args.adapter = adapter
                self.args.output_root = Path(self.temp.name) / adapter
                packet = self.prepared()
                outcome = self.outcome(packet)
                for event in outcome.result['toolEvidence']['events']:
                    event.update(toolName='exec_command', category='execute')
                report = probe.evaluate(packet, outcome, elapsed_ms=100, unchanged=True)
                self.assertEqual(report['status'], 'passed' if probe.system_sandbox(adapter) else 'failed')
                packet['systemSandbox'] = not packet['systemSandbox']
                self.assertEqual(probe.evaluate(packet, outcome, elapsed_ms=100, unchanged=True)['status'], 'failed')
