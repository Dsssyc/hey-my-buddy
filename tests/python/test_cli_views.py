"""The CLI prints brief model-facing projections without changing the service views.

These tests drive the real ``cli.main`` against the real governed transactions in
process, over real temporary Git checkouts: only the C-Two socket is replaced. They
check that the local ``output`` option never reaches RPC, that the brief views keep
every identifier the next command needs, and that ``"output":"full"`` prints the
unmodified service response.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

import test_workspace_lifecycle as lifecycle_tests
from test_workflow_real import CONFIGURATION, RealWorkspaceTestCase

from buddy import cli, cli_views, transport, workflow as workflow_module, workspace as workspace_module

PACKET = "Implement the bounded change described here. " * 40
SUMMARY = "Changed tracked.txt and ran the focused checks. " * 30


class CliViewTests(RealWorkspaceTestCase):
    register = lifecycle_tests.LifecycleTestCase.register
    claim = lifecycle_tests.LifecycleTestCase.claim
    finish_turn = lifecycle_tests.LifecycleTestCase.finish_turn
    view = lifecycle_tests.LifecycleTestCase.view
    target_with_artifact = lifecycle_tests.LifecycleTestCase.target_with_artifact

    def setUp(self) -> None:
        super().setUp()
        self.controls = {}
        self.worker = "w-views"
        self.enterContext(mock.patch.object(workflow_module, "_workspace_module", workspace_module))
        self.board_instance = self.board()
        self.register(self.board_instance)
        self.rpc_params: list[tuple[str, dict]] = []

        def request(_endpoint, operation, params, resource="control"):
            self.rpc_params.append((operation, dict(params)))
            return self.board_instance.call(operation, params)

        endpoint = {"address": "ipc://cli-views-test", "token": "test-token"}
        self.enterContext(mock.patch.object(transport, "ensure_service", lambda state_dir=None, resource="control": endpoint))
        self.enterContext(mock.patch.object(transport, "_request", request))
        self.enterContext(mock.patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory)}))
        for name in ("BUDDY_AGENT_CREDENTIAL", "BUDDY_AGENT_CREDENTIAL_FILE"):
            os.environ.pop(name, None)

    def turn_record(self, claim, *, disposition="completed"):
        record = lifecycle_tests.LifecycleTestCase.turn_record(self, claim, disposition=disposition)
        record["outcome"]["summary"] = SUMMARY
        return record

    def cli(self, method: str, params: dict) -> tuple[int, dict, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main([method, json.dumps(params)])
        text = output.getvalue()
        return code, json.loads(text), text

    def delivered(self) -> tuple[dict, dict]:
        """Submit through the CLI, then finish one sealed worker turn."""
        code, submitted, _ = self.cli("submit", {
            **CONFIGURATION, "requestId": "views-1", "hostId": "host-1", "task": PACKET, "cwd": str(self.repo),
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]},
        })
        self.assertEqual(code, 0, submitted)
        run_id = submitted["runId"]
        claimed = self.claim(self.board_instance, run_id, request_id="views-claim")
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
        (Path(manifest["path"]) / "tracked.txt").write_text("sealed output\n")
        seal = workspace_module.seal(self.directory, manifest, run_id, claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(self.board_instance, claimed, seal)
        return submitted, self.view(self.board_instance, run_id)

    def test_output_is_cli_local_and_validated_before_rpc(self):
        code, error, _ = self.cli("ping", {"output": "verbose"})
        self.assertEqual(code, 1)
        self.assertEqual(error["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.rpc_params, [])
        code, pong, text = self.cli("ping", {"output": "full"})
        self.assertEqual(code, 0, pong)
        self.assertNotIn("output", self.rpc_params[-1][1])
        self.assertEqual(text.count("\n"), 1, "one compact JSON line")

    def test_submit_receipt_names_the_control_file_without_the_packet(self):
        submitted, _view = self.delivered()
        self.assertEqual(submitted["view"], "receipt")
        self.assertTrue(submitted["controlFile"].endswith(f"{submitted['runId']}.g1.json"))
        self.assertNotIn("control", submitted)
        text = json.dumps(submitted)
        self.assertNotIn("controlToken", text)
        self.assertNotIn(PACKET[:40], text)
        for key in ("runId", "requestId", "revision", "ownerGeneration", "state", "workspace"):
            self.assertIn(key, submitted)

    def test_get_brief_keeps_next_command_identifiers_and_full_is_unchanged(self):
        submitted, service_view = self.delivered()
        run_id = submitted["runId"]
        code, brief, _ = self.cli("get", {"runId": run_id})
        self.assertEqual(code, 0, brief)
        self.assertEqual(brief["view"], "brief")
        self.assertEqual(brief["revision"], service_view["revision"])
        self.assertEqual(brief["finalArtifactId"], service_view["finalArtifactId"])
        output = next(row for row in service_view["artifacts"] if row["kind"] == "output")
        self.assertEqual(brief["artifacts"], [{key: output[key] for key in (
            "artifactId", "kind", "turnId", "manifestSha256", "outputCommit", "diffPath", "diffSha256", "changedPaths")
            if output.get(key)}])
        self.assertEqual(brief["otherArtifacts"], len(service_view["artifacts"]) - 1)
        self.assertEqual(brief["currentTurn"]["summary"], service_view["currentTurn"]["summary"])
        self.assertEqual(brief["workspace"]["path"], service_view["workspace"]["path"])
        self.assertEqual(brief["scope"]["scopeVersion"], service_view["scope"]["scopeVersion"])
        for omitted in ("goal", "executionWorkspace", "task", "turns", "requests", "taskId", "requestFingerprint"):
            self.assertNotIn(omitted, brief)
        code, full, _ = self.cli("get", {"runId": run_id, "output": "full"})
        self.assertEqual(code, 0, full)
        self.assertEqual(full, json.loads(json.dumps(self.view(self.board_instance, run_id))))
        code, audited, _ = self.cli("get", {"runId": run_id, "includeAudit": True})
        self.assertIn("audit", audited)

    def test_lifecycle_receipts_stay_small_and_complete(self):
        submitted, view = self.delivered()
        control_file = submitted["controlFile"]
        artifact = next(row for row in view["artifacts"] if row["kind"] == "output")
        target = self.target_with_artifact(artifact)
        code, recorded, _ = self.cli("integration-record", {
            "runId": view["runId"], "commandId": "views-int", "expectedRevision": view["revision"],
            "artifactId": artifact["artifactId"], "strategy": "patch", "beforeCommit": target["before"],
            "target": {"path": str(target["path"]), "ref": "HEAD"}, "reason": "verified", "controlFile": control_file,
        })
        self.assertEqual(code, 0, recorded)
        self.assertEqual(recorded["view"], "receipt")
        self.assertTrue(recorded["integrationId"])
        self.assertEqual(recorded["integration"]["integrationId"], recorded["integrationId"])
        self.assertNotIn("summary", recorded.get("currentTurn", {}))
        code, accepted, _ = self.cli("acknowledge", {
            "runId": view["runId"], "artifactId": artifact["artifactId"], "integrationId": recorded["integrationId"],
            "note": "inspected the sealed output", "controlFile": control_file,
        })
        self.assertEqual(code, 0, accepted)
        self.assertEqual(accepted["state"], "accepted")
        code, planned, _ = self.cli("workspace-cleanup-plan", {
            "runId": view["runId"], "commandId": "views-plan", "expectedRevision": accepted["revision"],
            "controlFile": control_file,
        })
        self.assertEqual(code, 0, planned)
        plan = planned["plan"]
        self.assertTrue(plan["eligible"])
        self.assertNotIn("cleanup", planned, "the plan is printed once")
        self.assertNotIn("evidence", plan)
        code, applied, text = self.cli("workspace-cleanup-apply", {
            "runId": view["runId"], "planId": plan["planId"], "commandId": "views-apply",
            "expectedRevision": planned["revision"], "confirmPath": plan["path"], "controlFile": control_file,
        })
        self.assertEqual(code, 0, applied)
        self.assertTrue(applied["removed"])
        self.assertEqual(applied["plan"]["state"], "applied")
        self.assertLess(len(text), 4000)

    def test_list_status_result_and_await_drop_duplicates(self):
        submitted, _view = self.delivered()
        run_id = submitted["runId"]
        code, listed, _ = self.cli("list", {"limit": 5})
        self.assertEqual(code, 0, listed)
        self.assertNotIn("tasks", listed)
        row = next(entry for entry in listed["runs"] if entry["runId"] == run_id)
        self.assertTrue(row["title"])
        self.assertLessEqual(len(row["title"]), 120)
        code, status, _ = self.cli("status", {"runId": run_id})
        self.assertEqual(code, 0, status)
        for omitted in ("spec", "task", "selectedAttempt"):
            self.assertNotIn(omitted, status)
        self.assertEqual(status["workflow"]["state"], "delivered")
        code, result, _ = self.cli("result", {"runId": run_id})
        self.assertEqual(code, 0, result)
        self.assertTrue(result["attemptId"])
        self.assertIn("resultMeta", result)
        code, waited, _ = self.cli("await", {"runId": run_id, "waitSeconds": 5})
        self.assertEqual(code, 0, waited)
        self.assertEqual(waited["outcome"], "completed")
        self.assertNotIn("note", waited)
        self.assertEqual(waited["turn"]["summary"], SUMMARY[:2000])
        self.assertNotIn("turn", waited["result"], "the lifted turn is printed once")


class ProjectionUnitTests(unittest.TestCase):
    def test_unconfirmed_shutdown_and_truncation_are_kept(self):
        view = {
            "governed": True, "runId": "r", "revision": 3, "state": "running", "status": "running",
            "awaitingHost": False, "shutdown": {"selfConfirmed": True, "descendantsConfirmed": False,
                                                 "unconfirmedCount": 2, "unconfirmedRunIds": ["a", "b"],
                                                 "truncated": False},
            "counts": {"children": 40, "turns": 1}, "truncated": {"children": 8, "turns": 0},
            "children": [{"taskId": "c", "role": "helper", "state": "running", "createdAt": "x"}],
        }
        brief = cli_views.governed_brief(view)
        self.assertEqual(brief["shutdown"], view["shutdown"])
        self.assertEqual(brief["truncated"], {"children": 8})
        self.assertEqual(brief["counts"], view["counts"])
        self.assertEqual(brief["children"], [{"taskId": "c", "role": "helper", "state": "running"}])

    def test_unknown_receipt_fields_are_not_dropped(self):
        receipt = cli_views.governed_receipt({"governed": True, "runId": "r", "revision": 1, "newField": {"x": 1}})
        self.assertEqual(receipt["newField"], {"x": 1})

    def test_error_envelopes_and_unprojected_commands_pass_through(self):
        error = {"error": {"code": "X", "message": "m"}}
        self.assertIs(cli_views.render("get", error), error)
        health = {"status": "ok", "capacity": {"totalLimit": 8}}
        self.assertIs(cli_views.render("health", health), health)
        full = {"governed": True, "runId": "r", "goal": {"task": "t"}}
        self.assertIs(cli_views.render("get", full, cli_views.OUTPUT_FULL), full)


if __name__ == "__main__":
    unittest.main()
