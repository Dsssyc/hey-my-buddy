"""The daemon sizes admission without starting idle execution processes."""
from __future__ import annotations

import os
from unittest.mock import patch

from support import BoardTestCase
from hey_my_buddy.blackboard.service.daemon import Daemon


class DaemonAdmissionTests(BoardTestCase):
    def test_machine_ceiling_is_store_admission_only(self):
        with patch.dict(os.environ, {"HMB_MAX_CONCURRENT": "3"}):
            daemon = Daemon(self.directory / "state")
        self.assertEqual(daemon.store.max_concurrent, 3)
        self.assertFalse(hasattr(daemon, "pool"))
        self.assertIsNone(daemon.scheduler)

    def test_construction_does_not_start_an_execution(self):
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.launch_execution") as launch:
            daemon = Daemon(self.directory / "state")
        launch.assert_not_called()
        self.assertFalse(hasattr(daemon, "_sweeper"))
