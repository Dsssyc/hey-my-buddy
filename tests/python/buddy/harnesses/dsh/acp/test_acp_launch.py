"""Owned ACP launch and native environment tests."""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

from hey_my_buddy.buddy.harnesses.discovery import native_environment
from hey_my_buddy.buddy.harnesses.dsh.acp.client import AcpClient
from hey_my_buddy.buddy.harnesses.dsh.acp.connection import stop_evidence
from hey_my_buddy.buddy.harnesses.dsh.acp.launch import (LaunchOwnershipError, LaunchRejected,
                                                        child_environment, launch)
from hey_my_buddy.protocol.process_handle import ProcessHandle

from .support import AcpTestCase


class LaunchTest(AcpTestCase):
    def test_missing_private_root_is_refused(self):
        with self.assertRaises(LaunchRejected):
            launch([sys.executable, "-c", "pass"], private_root=None)

    def test_spawn_failure_has_no_child(self):
        with self.assertRaises(OSError):
            launch([str(self.root / "missing-cli")], private_root=self.root)

    def test_child_environment_inherits_native_homes_and_permission_control(self):
        source = {"PATH": "/usr/bin:/bin", "HOME": "/users/person",
                  "DSH_HOME": "/users/person/native", "HMB_STATE_DIR": "/private/board"}
        extra = {"DSH_PERMISSION_MODE": "read-only"}
        expected = native_environment(source)
        expected.update(extra)
        self.assertEqual(expected, child_environment(extra, source=source))
        self.assertNotIn("HMB_STATE_DIR", expected)

    def test_spawned_child_receives_native_environment(self):
        argv = [sys.executable, "-c", "import json,os;print(json.dumps(dict(os.environ)))"]
        process, handle = launch(argv, private_root=self.root,
                                 extra_env={"DSH_PERMISSION_MODE": "read-only"})
        try:
            out, _ = process.communicate(timeout=30)
            self.assertEqual(0, process.returncode)
        finally:
            handle.wait(5)
            # A reaped Windows venv launcher can precede its Job becoming empty.
            self.assertTrue(handle.shutdown_confirmed(), stop_evidence(handle))
        child = json.loads(out.decode())
        self.assertEqual(str(self.home), child["HOME"])
        self.assertEqual(str(self.dsh_home), child["DSH_HOME"])
        self.assertEqual("read-only", child["DSH_PERMISSION_MODE"])
        self.assertTrue(stop_evidence(handle)["shutdownConfirmed"])

    def test_handle_construction_failure_stops_child_and_retains_evidence(self):
        from hey_my_buddy.protocol import process_identity
        original_terminate = ProcessHandle.terminate
        with mock.patch.object(ProcessHandle, "terminate", autospec=True,
                               side_effect=original_terminate) as stop:
            with mock.patch.object(process_identity, "_creation_time", side_effect=RuntimeError("identity failed")):
                with self.assertRaises(LaunchOwnershipError) as caught:
                    launch([sys.executable, "-c", "import time;time.sleep(30)"],
                           private_root=self.root)
        self.assertEqual(stop.call_count, 1)
        error = caught.exception
        self.assertTrue(error.evidence["shutdownConfirmed"], error.evidence)
        self.assertEqual("gone", error.evidence["groupObserved"])

    def test_connection_construction_failure_stops_child_and_retains_evidence(self):
        with mock.patch("hey_my_buddy.buddy.harnesses.dsh.acp.client.AcpConnection",
                        side_effect=RuntimeError("connection failed")):
            with self.assertRaises(LaunchOwnershipError) as caught:
                AcpClient.start(self.agent_argv(), private_root=self.root)
        self.assertTrue(caught.exception.evidence["shutdownConfirmed"],
                        caught.exception.evidence)
