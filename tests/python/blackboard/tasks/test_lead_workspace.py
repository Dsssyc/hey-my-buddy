"""A Lead controls workspace decisions for its own real-Git micro tasks."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from support import enable_fixture_buddy
from protocol.fixtures import native_turn
from blackboard.tasks.test_workflow_real import BUDDY, GIT_ENV, RealWorkspaceTestCase

from hey_my_buddy.blackboard.tasks import workflow as workflow_module, workspace
from hey_my_buddy.errors import BoardError


class LeadWorkspaceTests(RealWorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(patch.object(workflow_module, "_workspace_module", workspace))

    def _board_with_leads(self):
        board = self.board(max_concurrent=4)
        enable_fixture_buddy(board.store, BUDDY)
        other_buddy = {**BUDDY, "model": "deepseek-v4-pro"}
        enable_fixture_buddy(board.store, other_buddy)
        for worker in ("lead-worker", "other-lead-worker", "child-worker"):
            board.call("worker_register", {"workerId": worker, "capabilities": ["dsh"]})
        other_repo = self.directory / "other-repo"
        subprocess.run(["git", "clone", "-q", str(self.repo), str(other_repo)],
                       env={**os.environ, **GIT_ENV}, check=True)
        lead = self._lead(board, "lead", self.repo, "lead-worker")
        other = self._lead(board, "other-lead", other_repo, "other-lead-worker", buddy=other_buddy)
        return board, lead, other

    def _lead(self, board, request_id: str, repo: Path, worker: str, *, buddy=BUDDY) -> dict:
        submitted = board.call("workflow_submit", {
            **buddy, "requestId": request_id, "hostId": "host",
            "task": "Review and direct the child task", "cwd": str(repo),
            "submissionToken": request_id[0] * 32, "delegationAuthority": True,
            "executionWorkspace": {"kind": "existing", "access": "full", "accessReason": "Lead review"},
        })
        claimed = board.call("worker_claim", {
            "workerId": worker, "claimRequestId": f"claim-{request_id}",
            "nonce": worker[0] * 16, "runId": submitted["runId"],
        })["claim"]
        self.assertIsNotNone(claimed)
        return {"runId": submitted["runId"], "turnId": claimed["turn"]["turnId"],
                "credential": claimed["agentCredential"], "control": submitted["control"], "claim": claimed}

    def _child(self, board, lead: dict, request_id: str, *, scope=("src",)) -> dict:
        submitted = board.call("workflow_submit", {
            **BUDDY, "credential": lead["credential"], "requestId": request_id,
            "task": "Change the isolated checkout", "cwd": str(self.repo),
            "submissionToken": request_id[0] * 32,
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": list(scope)},
        })
        self.assertEqual(submitted["control"]["hostId"], f"lead:{lead['runId']}")
        with board.store.db.read() as connection:
            owner = connection.execute(
                "SELECT lead_run_id,lead_turn_id FROM workflow_lead_tasks WHERE owned_run_id=?",
                (submitted["runId"],),
            ).fetchone()
        self.assertEqual((owner["lead_run_id"], owner["lead_turn_id"]),
                         (lead["runId"], lead["turnId"]))
        return submitted

    def _claim_child(self, board, child: dict) -> tuple[dict, dict, Path]:
        response = board.call("worker_claim", {
            "workerId": "child-worker", "claimRequestId": f"claim-{child['runId']}",
            "nonce": "c" * 16, "runId": child["runId"],
        })
        claimed = response["claim"]
        self.assertIsNotNone(claimed, response)
        manifest = claimed["turn"]["input"]["executionWorkspace"]
        return claimed, manifest, Path(manifest["path"])

    def _finish_child(self, board, child: dict, claim: dict, *, seal: dict | None = None,
                      scope_failure: bool = False) -> None:
        attempt = claim["attempt"]
        result = {"turnResultPath": "/tmp/turn-result.json"}
        if scope_failure:
            result.update(status="failed", workspaceSealError="WORKSPACE_SCOPE_VIOLATION: outside write scope")
        else:
            result.update(status="ok", processState={"shutdownConfirmed": True},
                          turn=native_turn.claim_record(claim, outcome={
                              "disposition": "completed", "summary": "completed the child task",
                              "remaining": [], "decisions": [], "artifacts": [], "request": None,
                          }), workspaceSeal=seal)
        board.call("worker_result", {
            "workerId": "child-worker", "attemptId": attempt["attemptId"],
            "generation": attempt["generation"], "nonce": "c" * 16,
            "status": "failed" if scope_failure else "ok", "result": result,
            "shutdownConfirmed": True, "exitCode": 1 if scope_failure else 0,
            **({"error": result["workspaceSealError"]} if scope_failure else {}),
        })

    def _wrong_lead_refused(self, board, operation: str, params: dict, other: dict) -> None:
        with self.assertRaises(BoardError) as raised:
            board.call(operation, {**params, "credential": other["credential"]})
        self.assertEqual(raised.exception.code, "FORBIDDEN")

    def test_lead_can_amend_its_stopped_child_scope(self):
        board, lead, other = self._board_with_leads()
        child = self._child(board, lead, "child-amend")
        claim, manifest, _checkout = self._claim_child(board, child)
        seal = workspace.seal(self.directory, manifest, child["runId"], claim["attempt"]["attemptId"])
        self._finish_child(board, child, claim, seal=seal)
        view = board.call("workflow_get", {"runId": child["runId"]})
        params = {
            "runId": child["runId"], "commandId": "lead-amend",
            "expectedRevision": view["revision"], "expectedScopeVersion": 1,
            "writeScope": ["src", "docs"], "reason": "Documentation is in the next stage",
            **child["control"],
        }
        self._wrong_lead_refused(board, "workflow_scope_amend", params, other)
        amended = board.call("workflow_scope_amend", {**params, "credential": lead["credential"]})
        self.assertEqual(amended["scopeVersion"], 2)
        self.assertEqual(amended["writeScope"], ["docs", "src"])

    def test_lead_can_resolve_its_child_workspace_conflict(self):
        board, lead, other = self._board_with_leads()
        child = self._child(board, lead, "child-resolve")
        claim, manifest, checkout = self._claim_child(board, child)
        (checkout / "src" / "feature.py").write_text("value = 2\n")
        (checkout / "outside.txt").write_text("outside scope\n")
        with self.assertRaises(BoardError) as raised:
            workspace.seal(self.directory, manifest, child["runId"], claim["attempt"]["attemptId"])
        self.assertEqual(raised.exception.code, "WORKSPACE_SCOPE_VIOLATION")
        self._finish_child(board, child, claim, scope_failure=True)
        view = board.call("workflow_get", {"runId": child["runId"]})
        conflict = view["workspaceConflicts"][0]
        params = {
            "runId": child["runId"], "commandId": "lead-resolve",
            "expectedRevision": view["revision"], "conflictId": conflict["conflictId"],
            "action": "restore", "paths": ["outside.txt"],
            "observedFingerprint": conflict["observedFingerprint"],
            "reason": "Restore the unauthorized path", **child["control"],
        }
        self._wrong_lead_refused(board, "workflow_workspace_resolve", params, other)
        resolved = board.call("workflow_workspace_resolve", {**params, "credential": lead["credential"]})
        self.assertTrue(resolved["resolved"])
        self.assertFalse((checkout / "outside.txt").exists())
        self.assertEqual((checkout / "src" / "feature.py").read_text(), "value = 2\n")

    def test_lead_can_accept_and_reclaim_its_owned_child_checkout(self):
        board, lead, other = self._board_with_leads()
        child = self._child(board, lead, "child-reclaim")
        claim, manifest, checkout = self._claim_child(board, child)
        (checkout / "src" / "feature.py").write_text("value = 2\n")
        seal = workspace.seal(self.directory, manifest, child["runId"], claim["attempt"]["attemptId"])
        self._finish_child(board, child, claim, seal=seal)
        view = board.call("workflow_get", {"runId": child["runId"]})
        accepted = board.call("workflow_accept", {
            "runId": child["runId"], "artifactId": view["finalArtifactId"],
            "note": "Reviewed the sealed child output", "notRequired": "No integration target for this fixture",
            "keepCheckout": True, **child["control"], "credential": lead["credential"],
        })
        self.assertEqual(accepted["state"], "accepted")
        self.assertTrue(checkout.exists())
        params = {"runId": child["runId"], **child["control"]}
        self._wrong_lead_refused(board, "workflow_reclaim", params, other)
        reclaimed = board.call("workflow_reclaim", {**params, "credential": lead["credential"]})
        self.assertTrue(reclaimed["removed"])
        self.assertFalse(checkout.exists())

    def test_host_conclusion_of_lead_concludes_and_reclaims_owned_checkout(self):
        board, lead, _other = self._board_with_leads()
        child = self._child(board, lead, "child-auto-conclude")
        claim, manifest, checkout = self._claim_child(board, child)
        (checkout / "src" / "feature.py").write_text("value = 2\n")
        seal = workspace.seal(self.directory, manifest, child["runId"], claim["attempt"]["attemptId"])
        self._finish_child(board, child, claim, seal=seal)
        board.call("workflow_cancel", {"runId": lead["runId"], **lead["control"]})
        attempt = lead["claim"]["attempt"]
        board.call("worker_result", {
            "workerId": "lead-worker", "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "nonce": "l" * 16, "status": "cancelled", "result": {"status": "cancelled"},
            "shutdownConfirmed": True,
        })
        concluded = board.call("workflow_conclude", {"runId": lead["runId"], "note": "Stop this line of work", **lead["control"]})
        self.assertEqual(concluded["state"], "concluded")
        current = board.call("workflow_get", {"runId": child["runId"]})
        self.assertEqual(current["state"], "concluded")
        self.assertIn(lead["runId"], current["hostConclusion"]["note"])
        self.assertFalse(checkout.exists())
