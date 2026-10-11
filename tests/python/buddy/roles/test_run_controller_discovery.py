"""Discovery failure evidence crosses the existing controller receipt."""
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hey_my_buddy.buddy.roles import run_controller


class ControllerDiscoveryStopTests(unittest.TestCase):
    def receipt(self, stopped):
        error = ValueError('fixture native read failed')
        if stopped is not None:
            error.discovery_shutdown_confirmed = stopped
        def read(**kwargs):
            raise error
        control = {'operation': 'discover', 'harness': 'zcode', 'cwd': '/fixture',
                   'privateRoot': '/fixture', 'nativeRoot': '/fixture/native', 'timeoutSeconds': 1}
        with patch.object(run_controller, 'run_seam', return_value=SimpleNamespace(run_discovery=read)):
            frame, code = run_controller.execute(control, threading.Event(), Path('/fixture/state'))
        self.assertEqual(code, 1)
        return json.loads(frame)

    def test_failed_read_with_confirmed_stop_is_published(self):
        self.assertIs(self.receipt(True)['processState']['shutdownConfirmed'], True)

    def test_failed_read_with_unconfirmed_stop_is_published(self):
        self.assertIs(self.receipt(False)['processState']['shutdownConfirmed'], False)

    def test_failed_read_without_evidence_never_implies_stop(self):
        self.assertIs(self.receipt(None)['processState']['shutdownConfirmed'], False)
