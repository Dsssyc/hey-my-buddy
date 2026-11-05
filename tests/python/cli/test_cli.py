"""ADR-032 CLI surface and public creation requests."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.cli import main as cli
from hey_my_buddy.protocol import transport
from hey_my_buddy.protocol.requests import HOST_REQUESTS, OPERATIONS_REQUESTS, ROLE_REQUESTS


def invoke(method: str, payload: dict, *, document=None) -> tuple[int, dict]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = cli.main([method, json.dumps(payload)], credential_document=document)
    return status, json.loads(output.getvalue())


class SurfaceTests(unittest.TestCase):
    def test_public_registry_has_one_canonical_host_loop(self):
        self.assertEqual(set(HOST_REQUESTS), {
            "create", "amend", "submit", "await", "get", "inquire", "continue",
            "accept", "conclude", "cancel", "console",
        })
        self.assertEqual(set(cli.METHODS), set(HOST_REQUESTS) | set(OPERATIONS_REQUESTS) | set(ROLE_REQUESTS))
        self.assertEqual(set(transport.METHOD_MAP), set(cli.METHODS) - {"await", "install", "paths"})
        for retired in ("decide", "takeover", "reclaim", "goal-amend", "scope-amend",
                        "workspace-resolve", "status", "result", "events", "wait", "watch",
                        "execution-retry", "execution-acknowledge", "wait-capacity", "relocate"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, cli.METHODS)
                self.assertNotIn(retired, transport.METHOD_MAP)

    def test_private_observation_routes_do_not_become_cli_methods(self):
        self.assertEqual(transport.INTERNAL_METHOD_MAP["task_wait"], ("wait", "task_wait"))
        self.assertEqual(transport.INTERNAL_METHOD_MAP["events_wait"], ("wait", "events_wait"))
        for name in ("task_get", "task_result", "task_wait", "events_read", "events_wait", "workflow_get"):
            self.assertNotIn(name, cli.METHODS)

    def test_role_only_names_require_an_execution_credential_before_rpc(self):
        with mock.patch.object(cli, "_call_service", side_effect=AssertionError("must not call")):
            status, result = invoke("suggest", {"body": "check the file"})
        self.assertEqual(status, 1)
        self.assertEqual(result["error"]["code"], "FORBIDDEN")


class CreationRequestTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        state = self.root / "private-state"
        state.mkdir()
        state_patch = mock.patch.object(cli, "get_state_dir", return_value=state)
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def test_create_sends_one_validated_public_request(self):
        calls = []
        def service(method, params, state_dir):
            calls.append((method, dict(params)))
            return {"macroTaskId": "mt-1", "status": "open", "actions": ["amend", "submit", "get"]}
        with mock.patch.object(cli, "_call_service", side_effect=service):
            status, result = invoke("create", {"cwd": str(self.root), "title": "  Analysis  ", "goal": "Compare outputs"})
        self.assertEqual(status, 0, result)
        self.assertEqual(calls, [("create", {"cwd": str(self.root), "title": "Analysis", "goal": "Compare outputs"})])
        self.assertEqual(result["actions"], ["amend", "submit", "get"])

    def test_standalone_submit_sends_one_validated_public_request(self):
        calls = []
        def service(method, params, state_dir):
            calls.append((method, dict(params)))
            return {"runId": "run-1", "status": "queued", "actions": ["await", "inquire", "cancel"]}
        packet = {"cwd": str(self.root), "macroTaskId": "standalone", "title": "  Review  ",
                  "task": "  Read the changes  ", "buddy": "route", "checkout": "existing", "access": "read",
                  "inputs": [], "expectedSeconds": 600}
        with mock.patch.object(cli, "_call_service", side_effect=service):
            status, result = invoke("submit", packet)
        self.assertEqual(status, 0, result)
        self.assertEqual(calls, [("submit", {**packet, "title": "Review"})])

    def test_lead_submit_sends_one_validated_scoped_request(self):
        calls = []
        def service(method, params, state_dir):
            calls.append((method, dict(params)))
            return {"runId": "child", "status": "queued", "actions": ["await"]}
        packet = {"cwd": str(self.root), "title": "Child", "task": "Do the work",
                  "buddy": "dsh:provider:model:off", "checkout": "existing", "access": "read",
                  "inputs": [], "expectedSeconds": 300}
        document = {"token": "private", "stateDir": str(self.root), "taskId": "lead-run"}
        with mock.patch.object(cli, "_call_service", side_effect=service):
            status, _ = invoke("submit", packet, document=document)
        self.assertEqual(status, 0, _)
        self.assertEqual(calls, [("submit", {**packet, "credential": "private"})])


if __name__ == "__main__":
    unittest.main()
