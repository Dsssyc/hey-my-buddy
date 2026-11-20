"""Verify current receipt import and direct request responses after ADR-034."""
from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from admission import admit_board
from blackboard.store.upgrade_fixture import load_fixture, load_helper_retirement_stock, upgrade_fixture_board_to_41
from blackboard.tasks import test_lead_owned as lead_helpers
from blackboard.tasks.test_workflow import WorkflowTestCase
from blackboard.tasks.test_workflow_real import BUDDY, RealWorkspaceTestCase
from hey_my_buddy.blackboard.store.upgrade import upgrade_state
from hey_my_buddy.blackboard.tasks import macro_tasks, workspace
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.reasons import reason
from hey_my_buddy.protocol.workspace_manifest import turn_manifest
from support import BoardTestCase, InProcessBoard


def attention():
    return {
        "disposition": "attention",
        "summary": "The input needs review",
        "remaining": ["Apply the Host's review"],
        "decisions": [],
        "artifacts": [],
        "request": {
            "summary": "Review the input",
            "attempted": "Checked the input against the specification",
            "neededWork": "Choose the intended input",
            "expectedArtifacts": [],
            "acceptance": "The intended input is named",
        },
    }


class TurnRetirementTests(WorkflowTestCase):
    lead = lead_helpers.LeadOwnedTests.lead
    owned = lead_helpers.LeadOwnedTests.owned
    finish_owned = lead_helpers.LeadOwnedTests.finish_owned

    def test_retired_requests_have_no_service_side_effects_for_host_or_lead(self):
        board = self.board()
        host_run = self.submit(board, request_id="host-run")["runId"]
        _lead, lead_claim = self.lead(board)
        owned = self.owned(board, lead_claim)
        cases = ({"runId": host_run}, {"runId": owned["runId"], "credential": lead_claim["agentCredential"]})
        head = board.store.head()
        with patch.object(board.service, "_touch_harnesses") as discovery:
            for scope in cases:
                with self.subTest(lead="credential" in scope):
                    with self.assertRaises(BoardError) as error:
                        board.call("workflow_continue", {
                            **scope, "requestId": "unanswered", "input": "Continue work", "buddy": "route",
                            "note": "Route the continuation", "helperPolicy": "keep",
                        })
                    self.assertEqual(error.exception.code, "INVALID_ARGUMENT")
                    self.assertEqual(error.exception.details["field"], "helperPolicy")
            discovery.assert_not_called()
        self.assertEqual(board.store.head(), head)

    def test_lead_wakeup_failure_keeps_the_trigger_in_a_direct_request(self):
        board = self.board()
        lead, claim = self.lead(board)
        owned = self.owned(board, claim)
        self.finish_turn(board, {"claim": claim}, outcome={
            "disposition": "waiting", "summary": "Waiting for the owned result", "remaining": [],
            "decisions": [], "artifacts": [], "request": None,
        })
        workflow = board.store.workflow
        with patch.object(workflow, "_requeue", side_effect=BoardError("SHUTDOWN_UNCONFIRMED", "synthetic stop boundary")):
            self.finish_owned(board, owned)
        view = board.call("workflow_get", {"runId": lead["runId"]})
        self.assertEqual(view["state"], "awaiting-host")
        request = view["activeRequest"]
        self.assertEqual(request["reason"]["code"], "SHUTDOWN_UNCONFIRMED")
        self.assertEqual(request["trigger"]["runId"], owned["runId"])
        with board.store.db.read() as connection:
            wakeups = connection.execute(
                "SELECT continuation_id,state,trigger_event_json FROM workflow_continuations "
                "WHERE run_id=? AND kind='lead-wakeup'", (lead["runId"],),
            ).fetchall()
            self.assertEqual(len(wakeups), 1)
            self.assertEqual(wakeups[0]["state"], "recorded")
            self.assertEqual(json.loads(wakeups[0]["trigger_event_json"]), request["trigger"])
        board.call("workflow_continue", {
            "runId": lead["runId"], "requestId": request["requestId"], "input": "Review the owned result",
        })
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT state FROM workflow_continuations WHERE continuation_id=?",
                (wakeups[0]["continuation_id"],),
            ).fetchone()[0], "invalidated")

    def test_attention_is_answered_directly_and_replayed_once(self):
        board = self.board()
        run_id = self.submit(board)["runId"]
        claim = self.claim(board, run_id=run_id)
        self.finish_turn(board, claim, outcome=attention())
        view = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(view["state"], "awaiting-host")
        direct = view["activeRequest"]["requestId"]
        with board.store.db.write() as connection:
            workflow = board.store.workflow
            sibling = workflow._record_system_request(
                connection, workflow._run_row(connection, run_id), reason("SHUTDOWN_UNCONFIRMED"), {}, workflow.now(),
            )
        packet = {"runId": run_id, "requestId": direct, "input": "Use the documented input"}
        answer = board.call("workflow_continue", packet)
        replay = board.call("workflow_continue", packet)
        self.assertFalse(answer["alreadyResponded"])
        self.assertTrue(replay["alreadyResponded"])
        self.assertEqual(replay["continuationId"], answer["continuationId"])
        with self.assertRaises(BoardError) as refused:
            board.call("workflow_continue", {**packet, "requestId": sibling})
        self.assertEqual(refused.exception.code, "CONFLICT")
        with board.store.db.read() as connection:
            rows = connection.execute(
                "SELECT request_id,state,response_kind,response_id,resolution_json FROM workflow_requests WHERE run_id=?",
                (run_id,),
            )
            self.assertEqual({tuple(row) for row in rows}, {
                (request_id, "superseded", "continue", answer["continuationId"], None)
                for request_id in (direct, sibling)
            })

    def test_new_failed_receipt_cannot_import_a_retired_turn(self):
        variants = (
            {"version": 1},
            {"disposition": "assistance"},
            {"request": {**attention()["request"], "suggestedBuddyId": "obsolete"}},
        )
        for index, change in enumerate(variants):
            with self.subTest(change=change):
                board = self.board()
                run_id = self.submit(board, request_id=f"refusal-{index}", cwd=str(self.workdir(f"refusal-{index}")))["runId"]
                claim = self.claim(board, run_id=run_id)
                record = self.turn_record(claim, outcome=attention())
                if "version" in change:
                    record.update(change)
                else:
                    record["outcome"].update(change)
                response = self.finish_turn(board, claim, record=record, runner_status="failed", exit_code=1, seal=False)
                self.assertEqual(response["taskState"], "failed")
                attempt_id = claim["claim"]["attempt"]["attemptId"]
                with board.store.db.read() as connection:
                    stored = json.loads(connection.execute(
                        "SELECT result_json FROM attempts WHERE attempt_id=?", (attempt_id,),
                    ).fetchone()[0])
                    self.assertEqual(stored["result"]["microTaskError"]["code"], "TURN_INVALID")
                    self.assertIsNone(connection.execute(
                        "SELECT request_id FROM workflow_requests WHERE run_id=?", (run_id,),
                    ).fetchone())


class ResolutionResponseTests(RealWorkspaceTestCase):
    def test_resolve_and_continue_binds_only_the_addressed_request(self):
        board = self.board()
        run_id = board.call("workflow_submit", {
            "cwd": str(self.repo), "macroTaskId": "standalone", "title": "Edit scoped content",
            "task": "Edit the feature", "buddy": " / ".join(BUDDY.values()), "checkout": "existing",
            "access": "write", "writeScope": ["src"], "inputs": [], "expectedSeconds": 30,
        })["runId"]
        nonce = "n" * 32
        claim = admit_board(board, {"nonce": nonce, "runId": run_id})["claim"]
        attempt = claim["attempt"]
        manifest = turn_manifest(board.directory, claim["turn"]["input"])
        checkout = Path(manifest["checkoutRoot"])
        (checkout / "tracked.txt").write_text("outside the authorized scope\n", encoding="utf-8")
        with self.assertRaises(BoardError) as violation:
            workspace.seal(board.directory, manifest, run_id, attempt["attemptId"])
        self.assertEqual(violation.exception.code, "WORKSPACE_SCOPE_VIOLATION")
        error = "WORKSPACE_SCOPE_VIOLATION: managed changes outside the declared write scope"
        board.call("execution_result", {
            "attemptId": attempt["attemptId"], "generation": attempt["generation"], "nonce": nonce,
            "status": "failed", "error": error, "result": {"workspaceSealError": error},
            "shutdownConfirmed": True, "exitCode": 1,
        })
        view = board.call("workflow_get", {"runId": run_id})
        request_id = view["activeRequest"]["requestId"]
        conflict = view["workspaceConflicts"][0]
        with board.store.db.write() as connection:
            workflow = board.store.workflow
            sibling = workflow._record_system_request(
                connection, workflow._run_row(connection, run_id), reason("SHUTDOWN_UNCONFIRMED"), {}, workflow.now(),
            )
        packet = {
            "runId": run_id, "requestId": request_id, "resolution": "restore",
            "observedFingerprint": conflict["observedFingerprint"], "input": "Continue within src",
        }
        response = board.call("workflow_continue", packet)
        replay = board.call("workflow_continue", packet)
        self.assertFalse(response["alreadyResponded"])
        self.assertTrue(replay["alreadyResponded"])
        self.assertEqual(response["resolvedFingerprint"], replay["resolvedFingerprint"])
        self.assertEqual(response["continuationId"], replay["continuationId"])
        with board.store.db.read() as connection:
            rows = {row["request_id"]: row for row in connection.execute(
                "SELECT request_id,state,response_kind,response_id,resolution_json FROM workflow_requests WHERE run_id=?",
                (run_id,),
            )}
            direct = rows[request_id]
            self.assertEqual(tuple(direct)[1:4], ("resolved", "continue", response["continuationId"]))
            self.assertEqual(json.loads(direct["resolution_json"])["result"]["conflictId"], conflict["conflictId"])
            self.assertEqual(tuple(rows[sibling])[1:], ("superseded", "continue", response["continuationId"], None))


class RetainedTurnTests(BoardTestCase):
    def test_retained_assistance_validates_and_blocked_abandon_replays(self):
        fixture = load_fixture(self.directory / "retained")
        upgrade_fixture_board_to_41(fixture)
        load_helper_retirement_stock(fixture)
        upgrade_state(fixture["state"])
        board = InProcessBoard(fixture["state"])
        self.addCleanup(board.close)
        scenarios = fixture["helperRetirementStock"]["scenarios"]
        assistance = scenarios["suggestedAssistance"]
        blocked = scenarios["abandonBlocked"]
        with board.store.db.read() as connection:
            declined_run = scenarios["declined"]["parentRunId"]
            declined = next(marker for marker in macro_tasks._markers(connection, [declined_run])
                            if marker["eventKind"] == "workflow.request_declined")
            self.assertEqual(declined["runId"], declined_run)
            self.assertEqual(declined["requestId"], scenarios["declined"]["requestId"])
            turn = connection.execute("SELECT * FROM workflow_turns WHERE run_id=?", (assistance["runId"],)).fetchone()
            attempt = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (turn["attempt_id"],)).fetchone()
            outcome = board.store.workflow.turn_outcome(json.loads(attempt["result_json"]))
            self.assertEqual(outcome["version"], 1)
            self.assertEqual(outcome["outcome"]["disposition"], "assistance")
            self.assertIsNone(board.store.workflow._validate_retained_turn(outcome, turn, attempt))
            self.assertIsNotNone(board.store.workflow._validate_turn(outcome, turn, attempt))
            request = connection.execute(
                "SELECT * FROM workflow_requests WHERE request_id=?", (assistance["requestId"],),
            ).fetchone()
            view = board.store.workflow._request_view(request, include_audit=True, connection=connection)
            self.assertNotIn("suggestedBuddyId", view)
            self.assertNotIn("suggestedBuddyId", view["payload"])
            saved = json.loads(connection.execute(
                "SELECT resolution_json FROM workflow_requests WHERE request_id=?", (blocked["requestId"],),
            ).fetchone()[0])
        reply = board.call("workflow_continue", {
            "runId": blocked["runId"], "requestId": blocked["requestId"], **saved["parameters"],
        })
        self.assertTrue(reply["alreadyResponded"])
        self.assertTrue(reply["continuationBlocked"])
        self.assertEqual(reply["resolutionState"], "open")
        self.assertEqual(reply["artifactId"], blocked["artifactId"])


if __name__ == "__main__":
    unittest.main()
