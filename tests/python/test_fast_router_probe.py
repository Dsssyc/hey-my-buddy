"""The native probe is inert until execute, and each approved packet runs once."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hey_my_buddy.buddy.harnesses.base import AdapterOutcome

path = Path(__file__).resolve().parents[1] / 'probes' / 'router_fast.py'
spec = importlib.util.spec_from_file_location('fast_router_probe', path)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FastRouterProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fast-router-probe-test-')
        self.addCleanup(self.temp.cleanup)
        self.args = SimpleNamespace(adapter='dsh', provider='deepseek-official', model='deepseek-flash',
                                    effort='off', output_root=Path(self.temp.name) / 'probe', execute=False)

    def test_prepare_is_model_free_and_contains_no_repository_payload(self):
        with patch.object(probe.DecisionAdapter, 'start') as start:
            report = probe.run(self.args)
        start.assert_not_called()
        self.assertEqual(report['modelCalls'], 0)
        document = probe.packet(self.args)
        self.assertNotIn('executionWorkspace', document)
        self.assertNotIn('evidence', document)
        self.assertEqual(document['budget'], {'timeoutSeconds': 60})

    def test_execute_refuses_a_second_call_with_the_same_packet(self):
        self.args.execute = True
        handle = Mock()
        handle.wait.return_value = 0
        handle.shutdown_confirmed.return_value = True
        outcome = AdapterOutcome(status='ok', result={'zeroToolVerified': True}, shutdown_confirmed=True)
        with patch.object(probe.DecisionAdapter, 'start', return_value=handle) as start, \
                patch.object(probe.DecisionAdapter, 'collect', return_value=outcome):
            self.assertEqual(probe.run(self.args)['status'], 'passed')
            with self.assertRaises(FileExistsError):
                probe.run(self.args)
        start.assert_called_once()
        context = start.call_args.args[0]
        self.assertIsNone(context.agent_credential)
        self.assertEqual(Path(context.environment['BUDDY_STATE_DIR']).parent, self.args.output_root.resolve())
        self.assertEqual(Path(context.environment['BUDDY_RUNTIME_ROOT']).parent, self.args.output_root.resolve())

    def test_a_changed_packet_needs_a_new_output_root(self):
        probe.run(self.args)
        self.args.model = 'different-model'
        with self.assertRaisesRegex(ValueError, 'prepared packet differs'):
            probe.run(self.args)
