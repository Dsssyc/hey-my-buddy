"""Deletion clears receipts written by the real workflow command producers."""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from blackboard.tasks.test_workflow_real import RealWorkspaceTestCase
from blackboard.tasks import test_scope_recovery as scope_fixtures
from console.test_console import ConsoleTestCase

from hey_my_buddy.blackboard.tasks import macro_task_deletion
from hey_my_buddy.blackboard.tasks import workflow as workflow_module
from hey_my_buddy.blackboard.tasks import workspace as workspace_module


PHYSICAL_STAGES = (
    "_delete_sessions", "_delete_workspaces",
    "_delete_private_roots", "_delete_cli_files",
)
RESPONSE_BINDINGS = (
    "runId", "targetRunId", "requestId", "continuationId", "planId",
    "artifactId", "integrationId", "conclusionId", "conflictId", "macroTaskId",
)


def delete_without_physical_io(board, target):
    # The command assertion concerns the final database transaction. Other tests
    # exercise removal of actual worktrees, private files and native sessions.
    with ExitStack() as stack:
        for stage in PHYSICAL_STAGES:
            stack.enter_context(patch.object(macro_task_deletion, stage, return_value=[]))
        count = macro_task_deletion.preview(board.store, target)["microTaskCount"]
        return macro_task_deletion.delete(board.store, target, count)


def receipt_rows(board):
    with board.store.db.read() as connection:
        return {row["command_id"]: dict(row) for row in connection.execute("SELECT * FROM commands")}


def assert_deleted_receipts(case, board, target, expected_kinds, retained_command_ids, owned_run_ids):
    before = receipt_rows(board)
    owned = {command_id: row for command_id, row in before.items()
             if row["kind"] in expected_kinds and command_id not in retained_command_ids}
    case.assertEqual({row["kind"] for row in owned.values()}, set(expected_kinds))
    references = {field: set() for field in RESPONSE_BINDINGS}
    references["runId"].update(owned_run_ids)
    references["targetRunId"].update(owned_run_ids)
    references["macroTaskId"].add(target)
    for row in owned.values():
        response = json.loads(row["response_json"])
        for field in RESPONSE_BINDINGS:
            if isinstance(response.get(field), str):
                references[field].add(response[field])
    with board.store.db.read() as connection:
        attempt_ids = {row[0] for row in connection.execute(
            "SELECT attempt_id FROM attempts WHERE task_id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(owned_run_ids)),),
        )}
    deleted = delete_without_physical_io(board, target)
    case.assertEqual(deleted["state"], "deleted")
    after = receipt_rows(board)
    case.assertLessEqual(set(after), set(before))
    for command_id, original in owned.items():
        with case.subTest(command_id=command_id, kind=original["kind"]):
            case.assertNotIn(command_id, after)
    for command_id in retained_command_ids:
        case.assertEqual(after[command_id], before[command_id])
    for command_id, row in after.items():
        case.assertNotIn(row["task_id"], owned_run_ids, command_id)
        case.assertNotIn(row["attempt_id"], attempt_ids, command_id)
        response = json.loads(row["response_json"])
        for field, identifiers in references.items():
            value = response.get(field)
            if isinstance(value, str):
                case.assertNotIn(value, identifiers, (command_id, field))
    with board.store.db.read() as connection:
        case.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
    return before, after


class WorkflowDeletionCommandTests(WorkflowTestCase, ConsoleTestCase):
    def test_real_workflow_commands_are_removed_with_their_macro_task(self):
        board = self.board()
        first = self.submit(board, request_id="commands-first", macroTask={"title": "Disposable"})
        self.finish_turn(board, self.claim(board), disposition="attention")
        attention = board.call("workflow_get", {"runId": first["runId"]})
        decided = self.decide(board, attention, attention["activeRequest"]["requestId"],
                              command_id="commands-decide", decision="decline", autoContinue=False)
        self.continue_run(board, decided, command_id="commands-continue")
        self.finish_turn(board, self.claim(board, claim_request_id="commands-claim-2"))
        delivered = board.call("workflow_get", {"runId": first["runId"]})
        amended = board.store.workflow.scope_amend({
            "runId": first["runId"], "commandId": "commands-scope",
            "expectedRevision": delivered["revision"], "expectedScopeVersion": 1,
            "writeScope": ["docs"], "note": "Authorize reviewed follow-up",
            **self.control(delivered),
        })
        accepted = self.accept_run(board, amended, keepCheckout=True)
        self.assertEqual(accepted["state"], "accepted")

        second = self.submit(board, request_id="commands-second", macroTaskId=first["macroTaskId"])
        cancelled = board.call("workflow_cancel", {"runId": second["runId"],
                                                   "commandId": "commands-cancel", **self.control(second)})
        board.call("workflow_conclude", {"runId": second["runId"],
                                         "note": "Cancelled before execution", **self.control(cancelled)})

        other = self.submit(board, request_id="commands-other", macroTask={"title": "Retained"},
                            cwd=str(self.workdir("commands-other")))
        other_cancelled = board.call("workflow_cancel", {"runId": other["runId"],
                                                         "commandId": "commands-other-cancel", **self.control(other)})
        board.call("workflow_conclude", {"runId": other["runId"],
                                         "note": "Retained group", **self.control(other_cancelled)})

        _, browser = self.open_console(board)
        status, _, raw = browser.command(
            "macro_task_stop", {"macroTaskId": first["macroTaskId"], "commandId": "commands-stop"},
            csrf=browser.bootstrap()["csrfToken"],
        )
        self.assertEqual(status, 200, raw)
        retained = {command_id for command_id, row in receipt_rows(board).items()
                    if command_id == "commands-other-cancel"
                    or (row["kind"].startswith("workflow.")
                        and json.loads(row["response_json"]).get("runId") == other["runId"])}
        before, after = assert_deleted_receipts(self, board, first["macroTaskId"], {
            "workflow.decide", "workflow.continue", "workflow.scope_amend", "workflow.accept",
            "workflow.cancel", "workflow.conclude", "macro_task.stop",
        }, retained, {first["runId"], second["runId"]})
        self.assertNotIn("commands-stop", after)
        self.assertEqual(before["commands-other-cancel"], after["commands-other-cancel"])
        status, _, raw = browser.command(
            "macro_task_stop", {"macroTaskId": other["macroTaskId"], "commandId": "commands-stop"},
            csrf=browser.bootstrap()["csrfToken"],
        )
        self.assertEqual(status, 200, raw)
        self.assertEqual(receipt_rows(board)["commands-stop"]["kind"], "macro_task.stop")


class WorkspaceDeletionCommandTests(RealWorkspaceTestCase):
    register = scope_fixtures.ScopeRecoveryTestCase.register
    micro_task_submit = scope_fixtures.ScopeRecoveryTestCase.micro_task_submit
    control = scope_fixtures.ScopeRecoveryTestCase.control
    claim = scope_fixtures.ScopeRecoveryTestCase.claim
    scope_failure = scope_fixtures.ScopeRecoveryTestCase.scope_failure
    turn_record = scope_fixtures.ScopeRecoveryTestCase.turn_record
    finish_turn = scope_fixtures.ScopeRecoveryTestCase.finish_turn
    view = scope_fixtures.ScopeRecoveryTestCase.view
    resolve = scope_fixtures.ScopeRecoveryTestCase.resolve
    scoped_failure = scope_fixtures.ScopeRecoveryTestCase.scoped_failure

    def setUp(self):
        super().setUp()
        self.controls = {}
        self.worker = "w-deletion-commands"
        previous_workspace = workflow_module._workspace_module
        workflow_module._workspace_module = workspace_module
        self.addCleanup(setattr, workflow_module, "_workspace_module", previous_workspace)

    def test_workspace_resolution_command_is_tombstoned(self):
        board, submitted, _claimed, _manifest, _checkout = self.scoped_failure()
        view = self.view(board, submitted["runId"])
        conflict = view["workspaceConflicts"][0]
        resolved = self.resolve(board, view, conflict, "restore", paths=["outside.txt", "tracked.txt"],
                                command_id="commands-resolve", note="Restore refused paths")
        board.call("workflow_cancel", {"runId": submitted["runId"],
                                       "commandId": "commands-resolved-cancel", **self.control(resolved)})
        assert_deleted_receipts(self, board, "run:" + submitted["runId"],
                                {"workflow.workspace_resolve", "workflow.cancel"}, set(), {submitted["runId"]})

    def test_cleanup_plan_and_apply_commands_are_tombstoned(self):
        board = self.board()
        submitted = self.micro_task_submit(board, request_id="commands-cleanup", scope=(".",), kind="worktree")
        run_id = submitted["runId"]
        claimed = self.claim(board, run_id)
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
        checkout = Path(manifest["path"])
        (checkout / "tracked.txt").write_text("accepted output\n")
        seal = workspace_module.seal(self.directory, manifest, run_id, claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal)
        delivered = self.view(board, run_id)
        accepted = board.call("workflow_accept", {
            "runId": run_id, "artifactId": delivered["finalArtifactId"],
            "note": "Reviewed output", "notRequired": "No separate target in this fixture",
            "keepCheckout": True, **self.control(delivered),
        })
        planned = board.store.workflow.cleanup_plan({
            "runId": run_id, "commandId": "commands-plan", "expectedRevision": accepted["revision"],
            **self.control(accepted),
        })
        board.store.workflow.cleanup_apply({
            "runId": run_id, "planId": planned["plan"]["planId"], "commandId": "commands-apply",
            "expectedRevision": planned["targetRevision"], "confirmPath": planned["plan"]["path"],
            **self.control(accepted),
        })
        self.assertFalse(checkout.exists())
        assert_deleted_receipts(self, board, "run:" + run_id,
                                {"workflow.accept", "workflow.cleanup_plan", "workflow.cleanup_apply"}, set(),
                                {run_id})
