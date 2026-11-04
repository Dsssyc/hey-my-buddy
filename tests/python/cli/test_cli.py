"""ADR-032 CLI surface and deterministic creation identities."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
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


class CreationKeyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "subdir").mkdir()
        state = self.root / "private-state"
        state.mkdir()
        state_patch = mock.patch.object(cli, "get_state_dir", return_value=state)
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def test_create_replay_and_worktree_subdirectory_use_one_project_key(self):
        calls = []
        def service(method, params, state_dir):
            calls.append((method, dict(params)))
            return {"macroTaskId": "mt-1", "status": "open", "actions": ["amend", "submit", "get"]}
        with mock.patch.object(cli, "_call_service", side_effect=service):
            first = invoke("create", {"cwd": str(self.root), "title": "Analysis", "goal": "Compare outputs"})
            replay = invoke("create", {"cwd": str(self.root / "subdir"), "title": "  Analysis  ", "goal": "Different goal is not an amendment"})
            changed = invoke("create", {"cwd": str(self.root), "title": "Analysis 2", "goal": "Compare outputs"})
        self.assertEqual(first[0], replay[0], 0)
        self.assertEqual(calls[0][1]["creationKey"], calls[1][1]["creationKey"])
        self.assertEqual(calls[1][1]["title"], "Analysis")
        self.assertNotEqual(calls[0][1]["creationKey"], calls[2][1]["creationKey"])
        self.assertNotIn("creationKey", {"cwd": str(self.root), "title": "Analysis", "goal": "Compare outputs"})
        self.assertEqual(first[1]["actions"], ["amend", "submit", "get"])

    def test_standalone_submit_key_binds_project_title_and_task(self):
        calls = []
        def service(method, params, state_dir):
            calls.append(dict(params))
            return {"runId": "run-1", "status": "queued", "actions": ["await", "inquire", "cancel"]}
        packet = {"cwd": str(self.root), "macroTaskId": "standalone", "title": "Review",
                  "task": "  Read the changes  ", "buddy": "route", "checkout": "existing", "access": "read",
                  "inputs": [], "expectedSeconds": 600}
        with mock.patch.object(cli, "_call_service", side_effect=service):
            self.assertEqual(invoke("submit", packet)[0], 0)
            self.assertEqual(invoke("submit", {**packet, "cwd": str(self.root / "subdir")})[0], 0)
            self.assertEqual(invoke("submit", {**packet, "task": "Read new changes"})[0], 0)
        self.assertEqual(calls[0]["creationKey"], calls[1]["creationKey"])
        self.assertNotEqual(calls[0]["creationKey"], calls[2]["creationKey"])
        self.assertEqual(calls[0]["task"], "  Read the changes  ")
        self.assertNotIn("requestId", calls[0])
        self.assertNotIn("submissionToken", calls[0])

    def test_grouped_lead_key_reads_its_macro_binding(self):
        calls = []
        def service(method, params, state_dir):
            calls.append((method, dict(params)))
            if method == "workflow_get":
                return {"runId": "lead-run", "macroTaskId": "mt-9", "project": {"id": "a" * 64}}
            return {"runId": "child", "status": "queued", "actions": ["await"]}
        packet = {"cwd": str(self.root), "title": "Child", "task": "Do the work",
                  "buddy": "dsh:provider:model:off", "checkout": "existing", "access": "read",
                  "inputs": [], "expectedSeconds": 300}
        document = {"token": "private", "stateDir": str(self.root), "taskId": "lead-run"}
        with mock.patch.object(cli, "_call_service", side_effect=service):
            status, _ = invoke("submit", packet, document=document)
        self.assertEqual(status, 0, _)
        self.assertEqual(calls[0][0], "workflow_get")
        self.assertEqual(calls[0][1], {"runId": "lead-run", "credential": "private"})
        self.assertEqual(calls[1][0], "submit")
        self.assertEqual(calls[1][1]["credential"], "private")
        self.assertNotIn("macroTaskId", calls[1][1])
        self.assertEqual(calls[1][1]["creationKey"], cli._creation_key(
            ["adr032-submit-v1", "macro:mt-9", "Child", "Do the work"]))


if __name__ == "__main__":
    unittest.main()
