"""Host workflow methods reach the real service while the browser stays read only."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch

from support import InProcessBoard
from console.test_console import Browser
from blackboard.tasks.test_workflow_real import RealWorkspaceTestCase
import blackboard.tasks.test_scope_recovery as scope_recovery_tests
import blackboard.tasks.workspace_lifecycle_case as lifecycle_tests

from hey_my_buddy.cli import main as cli
from hey_my_buddy.protocol import transport
from hey_my_buddy.blackboard.tasks import workflow as workflow_module, workspace as workspace_module
from hey_my_buddy.console.server import CONSOLE_OPERATIONS
from hey_my_buddy.protocol.contracts import BuddyControl, CONTROL_NAME
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.service.service import CONTROL_OPERATIONS
from hey_my_buddy.paths import native_path


HOST_WORKSPACE_OPERATIONS = {
    "continue": "workflow_continue",
    "cancel": "workflow_cancel",
    "accept": "workflow_accept",
    "conclude": "workflow_conclude",
}
RETIRED_METHODS = ("scope-amend", "workspace-resolve", "reclaim")


class WorkspaceApiTests(RealWorkspaceTestCase):
    register = lifecycle_tests.LifecycleTestCase.register
    claim = lifecycle_tests.LifecycleTestCase.claim
    turn_record = lifecycle_tests.LifecycleTestCase.turn_record
    finish_turn = lifecycle_tests.LifecycleTestCase.finish_turn
    view = lifecycle_tests.LifecycleTestCase.view
    submit_run = lifecycle_tests.LifecycleTestCase.submit_run
    run_worktree = lifecycle_tests.LifecycleTestCase.run_worktree
    target_with_artifact = lifecycle_tests.LifecycleTestCase.target_with_artifact
    micro_task_submit = scope_recovery_tests.ScopeRecoveryTestCase.micro_task_submit
    scope_failure = scope_recovery_tests.ScopeRecoveryTestCase.scope_failure
    scoped_failure = scope_recovery_tests.ScopeRecoveryTestCase.scoped_failure

    def setUp(self):
        super().setUp()
        self.worker = "w-api"
        self.enterContext(patch.object(workflow_module, "_workspace_module", workspace_module))

    def cli_call(self, board: InProcessBoard, method: str, params: dict) -> tuple[int, dict]:
        endpoint = {"address": "ipc://private-api-test", "token": "test-token"}
        with patch.object(transport, "_ensure_service", return_value=endpoint), patch.object(
            transport, "_request",
            side_effect=lambda _endpoint, operation, values, resource="control", **kwargs: board.call(operation, values),
        ), contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            code = cli.main([method, json.dumps(params)])
        return code, json.loads(output.getvalue())

    def browser(self, board: InProcessBoard) -> tuple[Browser, str]:
        opened = board.call("console", {"action": "open"})
        browser = Browser(opened["url"])
        return browser, browser.bootstrap()["csrfToken"]

    def test_host_surfaces_and_worker_authority(self):
        for name, operation in HOST_WORKSPACE_OPERATIONS.items():
            with self.subTest(operation=operation):
                self.assertEqual(transport.METHOD_MAP[name], ("control", operation))
                self.assertIn(name, cli.METHODS)
                self.assertIn(operation, CONTROL_OPERATIONS)
                self.assertNotIn(operation, CONSOLE_OPERATIONS)
                self.assertTrue(callable(getattr(BuddyControl, operation)))
        for name in RETIRED_METHODS:
            self.assertNotIn(name, transport.METHOD_MAP)
            self.assertNotIn(name, cli.METHODS)

        board = self.board()
        submitted = self.micro_task_submit(board, request_id="worker-boundary", scope=("src",))
        claimed = self.claim(board, submitted["runId"])["claim"]
        credential = claimed["agentCredential"]
        packets = {
            "workflow_continue": {"runId": submitted["runId"], "turnId": claimed["turn"]["turnId"],
                                  "input": "forged worker continuation"},
            "workflow_cancel": {"runId": submitted["runId"]},
            "workflow_accept": {"runId": submitted["runId"], "artifactId": "forged-artifact",
                                "note": "forged acceptance", "notRequired": "fixture"},
            "workflow_conclude": {"runId": submitted["runId"], "note": "forged conclusion"},
        }
        for operation, packet in packets.items():
            with self.subTest(worker_operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, {**packet, "credential": credential})
            self.assertEqual(caught.exception.code, "FORBIDDEN")
            with self.subTest(unknown_operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, {"unknownField": True})
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_host_methods_reach_the_real_ctwo_resource(self):
        with self.daemon():
            endpoint = transport._read_endpoint(self.directory)
            self.assertIsNotNone(endpoint)
            with self.rpc_connection(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as control:
                for operation in HOST_WORKSPACE_OPERATIONS.values():
                    with self.subTest(operation=operation):
                        response = json.loads(getattr(control, operation)(json.dumps({
                            "token": endpoint["token"], "unknownField": True,
                        })))
                        self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")

    def test_console_refuses_host_resolution_while_cli_continues_the_same_run(self):
        board, submitted, _claimed, _manifest, checkout = self.scoped_failure()
        view = self.view(board, submitted["runId"])
        conflict = view["workspaceConflicts"][0]
        browser, csrf = self.browser(board)
        resolution = {
            "runId": view["runId"], "requestId": view["activeRequest"]["requestId"],
            "resolution": "restore", "paths": ["outside.txt", "tracked.txt"],
            "observedFingerprint": conflict["observedFingerprint"],
            "note": "Restore unauthorized paths while retaining the legal edit",
        }
        status, _headers, body = browser.command("workflow_continue", {
            **resolution, "consoleAuthority": {"sessionId": "forged"},
        }, csrf=csrf)
        self.assertEqual(status, 404, body)
        self.assertTrue((checkout / "outside.txt").exists())
        status, _headers, body = browser.command("workflow_continue", resolution, csrf=csrf)
        self.assertEqual(status, 404, body)
        self.assertEqual(json.loads(body)["error"]["code"], "METHOD_NOT_FOUND")
        code, resolved = self.cli_call(board, "continue", resolution)
        self.assertEqual(code, 0, resolved)
        self.assertIn("artifactId", resolved)
        self.assertEqual(self.view(board, view["runId"])["workspaceConflicts"][0]["state"], "restored")
        self.assertFalse((checkout / "outside.txt").exists())
        self.assertEqual((checkout / "src" / "feature.py").read_text(encoding="utf-8"), "value = 2\n")

        next_turn = {"runId": view["runId"], "artifactId": resolved["artifactId"],
                     "input": "Continue with documentation", "writeScope": ["src", "docs"],
                     "note": "Documentation is authorized for the next turn"}
        code, continued = self.cli_call(board, "continue", next_turn)
        self.assertEqual(code, 0, continued)
        self.assertEqual(self.view(board, view["runId"])["scope"]["writeScope"], ["src", "docs"])

    def test_cli_acceptance_records_integration_and_reclaims_without_control_file(self):
        board = self.board()
        submitted, _claimed, manifest, checkout, _seal, view, artifact = self.run_worktree(board)
        target = self.target_with_artifact(artifact)
        code, accepted = self.cli_call(board, "accept", {
            "runId": view["runId"], "artifactId": artifact["artifactId"],
            "note": "Inspected the sealed output and verified the target commit",
            "target": {"path": str(target["path"]), "ref": "HEAD"},
            "beforeCommit": target["before"],
        })
        self.assertEqual(code, 0, accepted)
        self.assertEqual(accepted["state"], "accepted")
        integration = self.view(board, view["runId"])["integrations"][0]
        self.assertEqual(integration["state"], "verified")
        self.assertEqual(integration["afterCommit"], target["after"])
        self.assertTrue(accepted["reclaim"]["removed"])
        self.assertFalse(checkout.exists())
        self.assertTrue(native_path(Path(artifact["diffPath"])).exists())
