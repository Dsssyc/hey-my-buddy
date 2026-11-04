"""A Lead controls workspace decisions for its own real-Git micro tasks."""
from __future__ import annotations
from hey_my_buddy.protocol.workspace_manifest import turn_manifest, record_reference
from admission import admit_board, admit_store

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from support import enable_fixture_buddy
from protocol.fixtures import native_turn
from blackboard.tasks.test_workflow_real import BUDDY, GIT_ENV, RealWorkspaceTestCase

from hey_my_buddy.blackboard.tasks import workflow as workflow_module, workspace
from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
from hey_my_buddy.cli.main import _creation_key
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
            pass  # Admission is owned by the service.
        other_repo = self.directory / "other-repo"
        subprocess.run(["git", "clone", "-q", str(self.repo), str(other_repo)],
                       env={**os.environ, **GIT_ENV}, check=True)
        lead = self._lead(board, "lead", self.repo, "lead-worker")
        other = self._lead(board, "other-lead", other_repo, "other-lead-worker", buddy=other_buddy)
        return board, lead, other

    def _lead(self, board, request_id: str, repo: Path, worker: str, *, buddy=BUDDY, kind="existing") -> dict:
        task = "Review and direct the child task"
        project_id = workspace.ensure_project_id(workspace.repository_at(str(repo), allow_parent=True))
        submitted = board.call("workflow_submit", {
            "cwd": str(repo), "macroTaskId": "standalone", "title": request_id, "task": task,
            "buddy": ":".join(buddy.values()), "checkout": kind, "access": "full",
            "accessNote": "Lead review", "writeScope": ["."], "inputs": [],
            "expectedSeconds": 1800, "delegationAuthority": True,
            "creationKey": _creation_key(["adr032-submit-v1", "project:" + project_id, request_id, task]),
        })
        claimed = admit_board(board, {'workerId': worker, 'claimRequestId': f'claim-{request_id}', 'nonce': worker[0] * 16, 'runId': submitted['runId']})["claim"]
        self.assertIsNotNone(claimed)
        return {"runId": submitted["runId"], "turnId": claimed["turn"]["turnId"],
                "credential": claimed["agentCredential"], "claim": claimed}

    def _child(self, board, lead: dict, request_id: str, *, scope=("src",)) -> dict:
        task = "Change the isolated checkout"
        project_id = workspace.ensure_project_id(workspace.repository_at(str(self.repo), allow_parent=True))
        submitted = board.call("workflow_submit", {
            "credential": lead["credential"], "cwd": str(self.repo), "title": request_id,
            "task": task, "buddy": ":".join(BUDDY.values()), "checkout": "worktree",
            "access": "write", "writeScope": list(scope), "inputs": [], "expectedSeconds": 1800,
            "creationKey": _creation_key(["adr032-submit-v1", "project:" + project_id, request_id, task]),
        })
        self.assertNotIn("control", submitted)
        with board.store.db.read() as connection:
            owner = connection.execute(
                "SELECT lead_run_id,lead_turn_id FROM workflow_lead_tasks WHERE owned_run_id=?",
                (submitted["runId"],),
            ).fetchone()
        self.assertEqual((owner["lead_run_id"], owner["lead_turn_id"]),
                         (lead["runId"], lead["turnId"]))
        return submitted

    def _claim_child(self, board, child: dict) -> tuple[dict, dict, Path]:
        response = admit_board(board, {'workerId': 'child-worker', 'claimRequestId': f"claim-{child['runId']}", 'nonce': 'c' * 16, 'runId': child['runId']})
        claimed = response["claim"]
        self.assertIsNotNone(claimed, response)
        manifest = turn_manifest(self.directory, claimed["turn"]["input"])
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
                          }), workspaceSeal=record_reference(self.directory, seal))
        board.call('execution_result', {'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': 'c' * 16, 'status': 'failed' if scope_failure else 'ok', 'result': result, 'shutdownConfirmed': True, 'exitCode': 1 if scope_failure else 0, **({'error': result['workspaceSealError']} if scope_failure else {})})

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
            "runId": child["runId"], "artifactId": view["finalArtifactId"],
            "input": "Continue with documentation", "writeScope": ["src", "docs"],
            "note": "Documentation is in the next stage",
        }
        self._wrong_lead_refused(board, "workflow_continue", params, other)
        board.call("workflow_continue", {**params, "credential": lead["credential"]})
        amended = board.call("workflow_get", {"runId": child["runId"]})
        self.assertEqual(amended["scope"]["scopeVersion"], 2)
        self.assertEqual(amended["scope"]["writeScope"], ["src", "docs"])

    def test_lead_can_resolve_its_child_workspace_conflict(self):
        board, lead, other = self._board_with_leads()
        child = self._child(board, lead, "child-resolve")
        claim, manifest, checkout = self._claim_child(board, child)
        (checkout / "src" / "feature.py").write_text("value = 2\n", encoding="utf-8", newline="\n")
        (checkout / "outside.txt").write_text("outside scope\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError) as raised:
            workspace.seal(self.directory, manifest, child["runId"], claim["attempt"]["attemptId"])
        self.assertEqual(raised.exception.code, "WORKSPACE_SCOPE_VIOLATION")
        self._finish_child(board, child, claim, scope_failure=True)
        view = board.call("workflow_get", {"runId": child["runId"]})
        conflict = view["workspaceConflicts"][0]
        params = {
            "runId": child["runId"], "requestId": view["activeRequest"]["requestId"],
            "resolution": "restore", "paths": ["outside.txt"],
            "observedFingerprint": conflict["observedFingerprint"],
            "note": "Restore the unauthorized path",
        }
        self._wrong_lead_refused(board, "workflow_continue", params, other)
        board.call("workflow_continue", {**params, "credential": lead["credential"]})
        resolved = board.call("workflow_get", {"runId": child["runId"]})
        self.assertEqual(resolved["workspaceConflicts"][0]["state"], "restored")
        self.assertFalse((checkout / "outside.txt").exists())
        self.assertEqual((checkout / "src" / "feature.py").read_text(encoding="utf-8"), "value = 2\n")

    def test_lead_can_accept_and_reclaim_its_owned_child_checkout(self):
        board, lead, other = self._board_with_leads()
        child = self._child(board, lead, "child-reclaim")
        claim, manifest, checkout = self._claim_child(board, child)
        (checkout / "src" / "feature.py").write_text("value = 2\n", encoding="utf-8", newline="\n")
        seal = workspace.seal(self.directory, manifest, child["runId"], claim["attempt"]["attemptId"])
        self._finish_child(board, child, claim, seal=seal)
        view = board.call("workflow_get", {"runId": child["runId"]})
        params = {"runId": child["runId"], "artifactId": view["finalArtifactId"],
                  "note": "Reviewed the sealed child output", "notRequired": "No integration target for this fixture"}
        self._wrong_lead_refused(board, "workflow_accept", params, other)
        accepted = board.call("workflow_accept", {**params, "credential": lead["credential"]})
        self.assertEqual(accepted["state"], "accepted")
        self.assertTrue(accepted["reclaim"]["removed"])
        self.assertFalse(checkout.exists())

    def _concludable_lead_with_owned_checkout(self):
        board, lead, _other = self._board_with_leads()
        child = self._child(board, lead, "child-auto-conclude")
        claim, manifest, checkout = self._claim_child(board, child)
        (checkout / "src" / "feature.py").write_text("value = 2\n", encoding="utf-8", newline="\n")
        seal = workspace.seal(self.directory, manifest, child["runId"], claim["attempt"]["attemptId"])
        self._finish_child(board, child, claim, seal=seal)
        board.call("workflow_cancel", {"runId": lead["runId"]})
        attempt = lead["claim"]["attempt"]
        board.call('execution_result', {'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': 'l' * 16, 'status': 'cancelled', 'result': {'status': 'cancelled'}, 'shutdownConfirmed': True})
        return board, lead, child, checkout

    def test_host_conclusion_of_lead_concludes_and_reclaims_owned_checkout(self):
        board, lead, child, checkout = self._concludable_lead_with_owned_checkout()
        reclaim = board.store.workflow._reclaim_checkout
        released = []
        def after_release(*, run_id):
            with board.store.db.read() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM workspace_reservations WHERE holder_task_id=?"
                                                    " AND state IN ('held','transferred')", (run_id,)).fetchone()[0], 0)
            released.append(run_id)
            return reclaim(run_id=run_id)
        with patch.object(board.store.workflow, '_reclaim_checkout', side_effect=after_release):
            concluded = board.call("workflow_conclude", {"runId": lead["runId"], "note": "Stop this line of work"})
        self.assertEqual(set(released), {lead['runId'], child['runId']})
        self.assertEqual(concluded["state"], "concluded")
        current = board.call("workflow_get", {"runId": child["runId"]})
        self.assertEqual(current["state"], "concluded")
        self.assertIsNone(current["hostConclusion"]["note"])
        self.assertEqual(current["hostConclusion"]["reason"], {"code": "lead.owned-concluded", "params": {"leadRunId": lead["runId"]}})
        self.assertFalse(checkout.exists())

    def test_console_conclusion_reclaims_owned_checkout_without_host_control(self):
        board, lead, child, checkout = self._concludable_lead_with_owned_checkout()
        concluded = board.store.workflow.conclude(
            {"runId": lead["runId"], "note": "Console closes Lead work"},
            console_authority={"sessionId": "fixture-console"},
        )
        self.assertEqual(concluded["state"], "concluded")
        self.assertEqual(concluded["state"], "concluded")
        self.assertEqual(board.call("workflow_get", {"runId": child["runId"]})["state"], "concluded")
        self.assertFalse(checkout.exists())

    def test_conclusion_reports_missing_repository_for_each_owned_checkout(self):
        board, lead, _other = self._board_with_leads()
        children = []
        for index in range(2):
            child = self._child(board, lead, f"moved-owned-{index}")
            claim, manifest, checkout = self._claim_child(board, child)
            (checkout / "src" / "feature.py").write_text(
                f"value = {index + 2}\n", encoding="utf-8", newline="\n")
            seal = workspace.seal(self.directory, manifest, child["runId"], claim["attempt"]["attemptId"])
            self._finish_child(board, child, claim, seal=seal)
            children.append((child, checkout))
        board.call("workflow_cancel", {"runId": lead["runId"]})
        attempt = lead["claim"]["attempt"]
        board.call("execution_result", {
            "attemptId": attempt["attemptId"], "generation": attempt["generation"], "nonce": "l" * 16,
            "status": "cancelled", "result": {"status": "cancelled"}, "shutdownConfirmed": True,
        })
        coordinator = board.store.workflow
        reclaim = coordinator._reclaim_checkout
        attempted = []

        def after_commit(*, run_id):
            if not attempted:
                self.repo.rename(self.directory / "moved-after-conclusion")
            result = reclaim(run_id=run_id)
            attempted.append((run_id, result))
            return result

        with patch.object(coordinator, "_reclaim_checkout", side_effect=after_commit):
            concluded = board.call("workflow_conclude", {
                "runId": lead["runId"], "note": "Close all owned work",
            })
        self.assertEqual(concluded["state"], "concluded")
        self.assertEqual({run_id for run_id, _ in attempted},
                         {lead["runId"], *(child["runId"] for child, _ in children)})
        for child, checkout in children:
            reclaim_result = next(item for run_id, item in attempted if run_id == child["runId"])
            self.assertEqual(reclaim_result["reasons"], ["repository-not-found"])
            self.assertTrue(checkout.exists())
            self.assertEqual(board.call("workflow_get", {"runId": child["runId"]})["state"], "concluded")

    def test_replaying_lead_conclusion_keeps_identity_and_maintenance_retries_reclaim(self):
        board, lead, child, checkout = self._concludable_lead_with_owned_checkout()
        coordinator = board.store.workflow
        reclaim = coordinator._reclaim_checkout

        def blocked_owned_reclaim(**kwargs):
            if kwargs["run_id"] == child["runId"]:
                return {"removed": False, "reasons": ["fixture-busy"]}
            return reclaim(**kwargs)

        params = {"runId": lead["runId"], "note": "Close and reclaim"}
        with patch.object(coordinator, "_reclaim_checkout", side_effect=blocked_owned_reclaim):
            first = board.call("workflow_conclude", params)
        self.assertEqual(first["state"], "concluded")
        self.assertTrue(checkout.exists())
        with board.store.db.read() as connection:
            conclusions = connection.execute("SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0]
        replayed = board.call("workflow_conclude", params)
        self.assertTrue(replayed["alreadyResponded"])
        self.assertEqual(replayed["conclusionId"], first["conclusionId"])
        self.assertTrue(checkout.exists())
        Reclamation(board.store).run()
        self.assertFalse(checkout.exists())
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0], conclusions)


    def test_running_lead_integrates_into_its_managed_checkout_without_holding_its_allocation_lock(self):
        board = self.board(max_concurrent=4)
        enable_fixture_buddy(board.store, BUDDY)
        lead = self._lead(board, 'managed-lead', self.repo, 'lead-worker', kind='worktree')
        lead_manifest = turn_manifest(self.directory, lead['claim']['turn']['input'])
        target = Path(lead_manifest['checkoutRoot'])
        with workspace._lock(target.parent, create=False):
            pass
        child = self._child(board, lead, 'managed-lead-child')
        claim, manifest, checkout = self._claim_child(board, child)
        (checkout / 'src' / 'feature.py').write_text('value = 2\n', encoding='utf-8', newline='\n')
        sealed = workspace.seal(self.directory, manifest, child['runId'], claim['attempt']['attemptId'])
        self._finish_child(board, child, claim, seal=sealed)
        subprocess.run(['git', '-C', str(target), 'apply', sealed['diffPath']], env={**os.environ, **GIT_ENV}, check=True)
        subprocess.run(['git', '-C', str(target), 'add', '.'], env={**os.environ, **GIT_ENV}, check=True)
        subprocess.run(['git', '-C', str(target), 'commit', '-qm', 'Lead integrated child'], env={**os.environ, **GIT_ENV}, check=True)
        view = board.call('workflow_get', {'runId': child['runId']})
        accepted = board.call('workflow_accept', {'runId': child['runId'], 'artifactId': view['finalArtifactId'],
            'target': {'path': str(target), 'ref': 'HEAD'}, 'note': 'Lead reviewed its integration', 'credential': lead['credential']})
        self.assertEqual(accepted['state'], 'accepted')
        self.assertTrue(accepted['reclaim']['removed'], accepted)
        self.assertTrue(target.exists())
        self.assertEqual((target / 'src' / 'feature.py').read_text(encoding='utf-8'), 'value = 2\n')
        with workspace._lock(target.parent, create=False):
            pass
        with board.store.db.read() as connection:
            self.assertNotEqual(connection.execute('SELECT execution_state FROM attempts WHERE attempt_id=?',
                (lead['claim']['attempt']['attemptId'],)).fetchone()[0], 'finished')
