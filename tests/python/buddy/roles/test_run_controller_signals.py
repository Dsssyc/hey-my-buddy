"""The remaining model-discovery entry installs handlers only when executed."""
from __future__ import annotations

import importlib
import signal
import unittest
from unittest import mock

CONTROLLER = "hey_my_buddy.buddy.roles.run_controller"


class DiscoverySignalTests(unittest.TestCase):
    def test_import_preserves_the_callers_signal_handlers(self):
        signals = (signal.SIGTERM, signal.SIGINT,
                   *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ()))
        before = {sig: signal.getsignal(sig) for sig in signals}
        with mock.patch.object(signal, "signal", wraps=signal.signal) as install:
            module = importlib.import_module(CONTROLLER)
            importlib.reload(module)
        install.assert_not_called()
        self.assertEqual({sig: signal.getsignal(sig) for sig in signals}, before)


if __name__ == "__main__":
    unittest.main()
