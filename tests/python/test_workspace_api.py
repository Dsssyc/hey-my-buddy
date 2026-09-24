"""Public lifecycle operations against private real-Git workspaces."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import c_two as cc

from support import InProcessBoard
from test_console import Browser
from test_workflow_real import RealWorkspaceTestCase
import test_scope_recovery as scope_recovery_tests
import test_workspace_lifecycle as lifecycle_tests

from buddy import cli, transport, workflow as workflow_module, workspace as workspace_module
from buddy.console import CONSOLE_OPERATIONS
from buddy.contracts import BuddyControl, CONTROL_NAME
from buddy.errors import BoardError
from buddy.service import CONTROL_OPERATIONS


OPERATIONS = {
    "scope-amend": "workflow_scope_amend",
    "workspace-resolve": "workflow_workspace_resolve",
    "integration-record": "workflow_integration_record",
    "workspace-cleanup-plan": "workspace_cleanup_plan",
    "workspace-cleanup-apply": "workspace_cleanup_apply",
}


class WorkspaceApiTests(RealWorkspaceTestCase):
    register = lifecycle_tests.LifecycleTestCase.register
    control = lifecycle_tests.LifecycleTestCase.control
    claim = lifecycle_tests.LifecycleTestCase.claim
    turn_record = lifecycle_tests.LifecycleTestCase.turn_record
    finish_turn = lifecycle_tests.LifecycleTestCase.finish_turn
    view = lifecycle_tests.LifecycleTestCase.view
    run_worktree = lifecycle_tests.LifecycleTestCase.run_worktree
    target_with_artifact = lifecycle_tests.LifecycleTestCase.target_with_artifact
    governed_submit = scope_recovery_tests.ScopeRecoveryTestCase.governed_submit
    scope_failure = scope_recovery_tests.ScopeRecoveryTestCase.scope_failure
    scoped_failure = scope_recovery_tests.ScopeRecoveryTestCase.scoped_failure

    def setUp(self):
        super().setUp()
        self.controls = {}
        self.worker = "w-api"
        self.enterContext(patch.object(workflow_module, "_workspace_module", workspace_module))

    def cli_call(self, board: InProcessBoard, method: str, params: dict) -> tuple[int, dict]:
        endpoint = {"address": "ipc://private-api-test", "token": "test-token"}
        with patch.object(transport, "ensure_service", return_value=endpoint), patch.object(
            transport, "_request", side_effect=lambda _endpoint, operation, values, resource="control": board.call(operation, values)
        ), contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            code = cli.main([method, json.dumps(params)])
        return code, json.loads(output.getvalue())

    def browser(self, board: InProcessBoard) -> tuple[Browser, str]:
        opened = board.call("console", {"action": "open"})
        browser = Browser(opened["url"])
        return browser, browser.bootstrap()["csrfToken"]

    def test_five_named_surfaces_and_worker_authority(self):
        self.assertEqual({name: transport.METHOD_MAP[name] for name in OPERATIONS},
                         {name: ("control", operation) for name, operation in OPERATIONS.items()})
        for name, operation in OPERATIONS.items():
            with self.subTest(operation=operation):
                self.assertIn(name, cli.METHODS)
                self.assertIn(name, cli.CONTROL_METHODS)
                self.assertIn(operation, CONTROL_OPERATIONS)
                self.assertIn(operation, CONSOLE_OPERATIONS)
                self.assertTrue(callable(getattr(BuddyControl, operation)))

        board = self.board()
        self.register(board)
        submitted = self.governed_submit(board, request_id="worker-boundary", scope=("src",))
        claim = self.claim(board, submitted["runId"])["claim"]
        credential = claim["agentCredential"]
        for operation in OPERATIONS.values():
            with self.subTest(worker_operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, {"runId": submitted["runId"], "commandId": "worker-forged",
                                       "expectedRevision": submitted["revision"], "credential": credential})
            self.assertEqual(caught.exception.code, "FORBIDDEN")
            with self.subTest(unknown_operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, {"unknownField": True})
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_five_named_methods_reach_the_real_ctwo_resource(self):
        with self.daemon():
            endpoint = transport._read_endpoint(self.directory)
            self.assertIsNotNone(endpoint)
            with cc.connect(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as control:
                for operation in OPERATIONS.values():
                    with self.subTest(operation=operation):
                        response = json.loads(getattr(control, operation)(json.dumps({
                            "token": endpoint["token"], "unknownField": True,
                        })))
                        self.assertEqual(response["error"]["code"], "INVALID_ARGUMENT")

    def test_authenticated_http_scope_and_conflict_resolution(self):
        board, submitted, _claimed, _manifest, checkout = self.scoped_failure()
        view = self.view(board, submitted["runId"])
        conflict = view["workspaceConflicts"][0]
        browser, csrf = self.browser(board)
        resolution = {
            "runId": view["runId"], "commandId": "http-resolve", "expectedRevision": view["revision"],
            "conflictId": conflict["conflictId"], "action": "restore",
            "paths": ["outside.txt", "tracked.txt"],
            "observedFingerprint": conflict["observedFingerprint"],
            "reason": "Restore the unauthorized paths while retaining the legal edit",
        }
        status, _headers, body = browser.command("workflow_workspace_resolve", {
            **resolution, "consoleAuthority": {"sessionId": "forged"},
        }, csrf=csrf)
        self.assertEqual(status, 400, body)
        self.assertTrue((checkout / "outside.txt").exists())
        status, _headers, body = browser.command("workflow_workspace_resolve", resolution, csrf=csrf)
        self.assertEqual(status, 200, body)
        resolved = json.loads(body)["result"]
        self.assertTrue(resolved["resolved"])
        self.assertFalse((checkout / "outside.txt").exists())
        self.assertEqual((checkout / "src" / "feature.py").read_text(), "value = 2\n")
        self.assertNotIn("controlToken", json.dumps(resolved))

        scope = {"runId": resolved["runId"], "commandId": "http-amend",
                 "expectedRevision": resolved["revision"], "expectedScopeVersion": 1,
                 "writeScope": ["src", "docs"], "reason": "Host authorized documentation changes for the next stage"}
        status, _headers, body = browser.command("workflow_scope_amend", scope, csrf=csrf)
        self.assertEqual(status, 200, body)
        amended = json.loads(body)["result"]
        self.assertEqual(amended["scopeVersion"], 2)
        self.assertNotIn("controlToken", json.dumps(amended))

    def test_cli_integration_acceptance_and_cleanup_preserve_private_control(self):
        board = self.board()
        self.register(board)
        submitted, _claimed, manifest, checkout, _seal, view, artifact = self.run_worktree(board)
        control_file = cli._save_control(submitted["runId"], submitted["control"])
        target = self.target_with_artifact(artifact)
        integration_params = {
            "runId": view["runId"], "commandId": "cli-integrate", "expectedRevision": view["revision"],
            "artifactId": artifact["artifactId"], "strategy": "patch", "beforeCommit": target["before"],
            "target": {"path": str(target["path"]), "ref": "HEAD"},
            "reason": "Host applied the sealed patch to the verified target", "controlFile": control_file,
        }
        code, integrated = self.cli_call(board, "integration-record", integration_params)
        self.assertEqual(code, 0, integrated)
        self.assertEqual(integrated["integration"]["state"], "verified")
        self.assertEqual(integrated["integration"]["afterCommit"], target["after"])
        self.assertNotIn(submitted["control"]["controlToken"], json.dumps(integrated))

        code, refused = self.cli_call(board, "workspace-cleanup-plan", {
            "runId": view["runId"], "commandId": "missing-control", "expectedRevision": integrated["revision"],
        })
        self.assertEqual(code, 1)
        self.assertEqual(refused["error"]["code"], "UNAUTHORIZED")
        code, stale = self.cli_call(board, "workspace-cleanup-plan", {
            "runId": view["runId"], "commandId": "stale-control", "expectedRevision": integrated["revision"],
            "hostId": submitted["control"]["hostId"], "ownerGeneration": 2,
            "controlToken": submitted["control"]["controlToken"],
        })
        self.assertEqual(code, 1)
        self.assertEqual(stale["error"]["code"], "STALE_GENERATION")

        code, accepted = self.cli_call(board, "acknowledge", {
            "runId": view["runId"], "commandId": "cli-accept", "artifactId": artifact["artifactId"],
            "integrationId": integrated["integration"]["integrationId"],
            "note": "Inspected the sealed output and verified the target commit", "verdict": "accepted",
            "controlFile": control_file,
        })
        self.assertEqual(code, 0, accepted)
        self.assertEqual(accepted["state"], "accepted")
        self.assertNotIn(submitted["control"]["controlToken"], json.dumps(accepted))

        code, planned = self.cli_call(board, "workspace-cleanup-plan", {
            "runId": view["runId"], "commandId": "cli-plan", "expectedRevision": accepted["revision"],
            "controlFile": control_file,
        })
        self.assertEqual(code, 0, planned)
        plan = planned["plan"]
        self.assertTrue(plan["eligible"])
        self.assertEqual(plan["path"], manifest["checkoutRoot"])
        self.assertTrue(checkout.exists())
        code, applied = self.cli_call(board, "workspace-cleanup-apply", {
            "runId": view["runId"], "planId": plan["planId"], "commandId": "cli-apply",
            "expectedRevision": planned["revision"], "confirmPath": plan["path"],
            "controlFile": control_file,
        })
        self.assertEqual(code, 0, applied)
        self.assertTrue(applied["removed"])
        self.assertFalse(checkout.exists())
        self.assertTrue(Path(artifact["diffPath"]).exists())
        self.assertNotIn(submitted["control"]["controlToken"], json.dumps(applied))
