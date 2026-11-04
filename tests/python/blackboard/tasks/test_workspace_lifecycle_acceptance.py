"""Acceptance derives and verifies its integration over real Git checkouts.

One ``accept`` must verify the exact final artifact against its recorded target
(or record an explicit not-required decision), whatever the artifact's size and
history, and a target that moved under the review is refused.
"""
from __future__ import annotations
from hey_my_buddy.protocol.workspace_manifest import turn_manifest
from unittest.mock import patch as defer_reclaim
import json
import os
from pathlib import Path
from unittest import mock
from blackboard.tasks.test_workflow_real import BUDDY
from hey_my_buddy.blackboard.tasks import workflow as workflow_module
from hey_my_buddy.blackboard.tasks import workspace as workspace_module
from hey_my_buddy.blackboard.store.db import canonical_json
from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.roles import turn_io
from hey_my_buddy.errors import BoardError

from blackboard.tasks.workspace_lifecycle_case import LifecycleTestCase, git
from hey_my_buddy.paths import native_path


class AcceptanceLifecycleTests(LifecycleTestCase):
    def test_accept_replay_retains_the_requested_subdirectory_target(self):
        board = self.board()
        _submitted,_claimed,_manifest,_checkout,_seal,view,artifact = self.run_worktree(board)
        target = self.target_with_artifact(artifact)
        nested = target["path"] / "nested"
        native_path(nested).mkdir()
        packet = {"runId":view["runId"],"artifactId":artifact["artifactId"],"note":"Reviewed this exact target",
                  "target":{"path":str(nested),"ref":"HEAD"},"beforeCommit":target["before"]}
        first = board.call("workflow_accept",packet)
        with mock.patch.object(workspace_module,"integration_verify",side_effect=AssertionError("replay must not verify again")):
            replay = board.call("workflow_accept",packet)
        self.assertTrue(replay["alreadyResponded"])
        self.assertEqual(first["integrationId"],replay["integrationId"])
        with board.store.db.read() as connection:
            row = connection.execute("SELECT target_path,verification_json FROM workflow_integrations WHERE integration_id=?",
                                     (first["integrationId"],)).fetchone()
        self.assertEqual(row["target_path"],str(target["path"].resolve()))
        self.assertEqual(json.loads(row["verification_json"])["requested"]["target"],packet["target"])
        with self.assertRaises(BoardError) as refused:
            board.call("workflow_accept",{**packet,"target":{"path":str(target["path"]),"ref":"HEAD"}})
        self.assertEqual(refused.exception.code,"CONFLICT")

    # -- acceptance carries its integration evidence -------------------------
    def test_acceptance_requires_a_verified_or_not_required_decision(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(board)
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": view["runId"], "artifactId": artifact["artifactId"],
                "note": "inspected",
            })
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as raised:
            self.accept(board, view, artifact, not_required=True, reason="")
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": view["runId"], "artifactId": artifact["artifactId"], "note": "no",
                "notRequired": "no", "target": {"path": str(self.repo), "ref": "HEAD"},
            })
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        accepted = self.accept(board, view, artifact, not_required=True, reason="no separate target")
        self.assertFalse(accepted["alreadyResponded"])
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(self.view(board,submitted["runId"])["integrations"][0]["state"], "not-required")
        self.assertIsNone(self.view(board,submitted["runId"])["integrations"][0]["target"])
        self.assertEqual(self.view(board,submitted["runId"])["integrations"][0]["artifactId"], artifact["artifactId"])
        duplicate = board.call("workflow_accept", {
            "runId": view["runId"], "artifactId": artifact["artifactId"],
            "note": "inspected the sealed output", "notRequired": "no separate target",
        })
        self.assertTrue(duplicate["alreadyResponded"])
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": view["runId"], "artifactId": artifact["artifactId"],
                "note": "a different review", "notRequired": "no separate target",
            })
        self.assertEqual(raised.exception.code, "CONFLICT")

    def test_a_stale_output_record_never_accepts_a_newer_final_artifact(self):
        board = self.board()
        self.register(board)
        submitted, first_claim, manifest, checkout, first_seal, yielded, first_artifact = self.run_worktree(
            board, request_id="two-turns-1", output="first output\n", disposition="assistance"
        )
        run_id = submitted["runId"]
        self.assertEqual(yielded["state"], "awaiting-host")
        # The Host continues; a second turn seals the new final output.
        continued = self.continue_run(board, {'runId': run_id, 'input': "finish the micro task"})
        self.assertEqual(continued["state"], "executing")
        second = self.claim(board, run_id, request_id="two-turns-claim-2", nonce="q" * 16)
        self.assertIsNotNone(second["claim"], second)
        second_manifest = turn_manifest(self.directory, second["claim"]["turn"]["input"])
        (checkout / "tracked.txt").write_text("second output\n", encoding="utf-8", newline="\n")
        second_seal = workspace_module.seal(self.directory, second_manifest, run_id,
                                            second["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, second, second_seal, nonce="q" * 16)
        delivered = self.view(board, run_id)
        self.assertEqual(delivered["state"], "delivered")
        outputs = [row for row in delivered["artifacts"] if row["kind"] == "output"]
        self.assertEqual(len(outputs), 2)
        first_output, final_output = outputs[-1], outputs[0]
        # Acceptance binds the exact current final artifact; an older output is
        # refused instead of standing in for it.
        with self.assertRaises(BoardError) as raised:
            self.accept(board, delivered, first_output, not_required=True, reason="older output")
        self.assertEqual(raised.exception.code, "CONFLICT")
        current = self.view(board, run_id)
        accepted = self.accept(board, current, final_output, not_required=True, reason="final output")
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["finalArtifactId"], final_output["artifactId"])

    def test_verified_integration_binds_the_actual_target_checkout(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(board)
        target = self.target_with_artifact(artifact)
        # Before any acceptance, a target that never received the artifact is
        # refused with the differing paths; obsolete target IDs are rejected at
        # the request boundary. Host adjustments need adjusted:true and a note.
        blank = self.directory / "blank"
        git(self.directory, "clone", "-q", str(self.repo), str(blank))
        with self.assertRaises(BoardError) as raised:
            self.accept(board, view, artifact, target={"path": blank, "before": None})
        self.assertEqual(raised.exception.code, "INTEGRATION_UNVERIFIED")
        self.assertIn("tracked.txt", raised.exception.details["paths"])
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": view["runId"], "artifactId": artifact["artifactId"], "note": "x",
                "target": {"path": str(target["path"]), "ref": "HEAD", "repositoryId": "0" * 64},
            })
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": view["runId"], "artifactId": "art-not-real", "note": "x",
                "notRequired": "unknown",
            })
        self.assertEqual(raised.exception.code, "NOT_FOUND")
        recorded = self.accept(board, view, artifact, target=target, reason="merged the sealed patch")
        integration = recorded["integrations"][0]
        self.assertEqual(integration["state"], "verified")
        self.assertEqual(integration["target"]["path"], str(Path(target["path"]).resolve()))
        self.assertEqual(integration["target"]["projectId"], workspace_module.inspect(str(target["path"]))["projectId"])
        self.assertEqual(integration["beforeCommit"], target["before"])
        self.assertEqual(integration["afterCommit"], target["after"])
        self.assertEqual(integration["beforeTree"], git(target["path"], "rev-parse", target["before"] + "^{tree}").strip())
        self.assertEqual(integration["afterTree"], git(target["path"], "rev-parse", target["after"] + "^{tree}").strip())
        self.assertEqual(integration["sourceCommit"], artifact["outputCommit"])
        self.assertEqual(integration["verification"]["matchingPaths"], ["tracked.txt"])
        self.assertEqual(integration["verification"]["missingPaths"], [])
        self.assertEqual(recorded["state"], "accepted")
        # After the acceptance, any changed payload conflicts against the recorded
        # review instead of relabelling it.
        current = self.view(board, submitted["runId"])
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": current["runId"], "artifactId": artifact["artifactId"],
                "note": "a different review",
                "target": {"path": str(target["path"]), "ref": "HEAD"},
                "beforeCommit": target["before"],
            })
        self.assertEqual(raised.exception.code, "CONFLICT")

    def test_thousand_file_rename_seals_patches_accepts_and_reclaims(self):
        self.rename_lifecycle()

    def test_receipt_larger_than_rpc_limit_seals_accepts_and_reclaims(self):
        self.rename_lifecycle(long_paths=True)

    def test_historical_truncated_seal_accepts_and_reclaims_without_rewriting(self):
        self.rename_lifecycle(historical=True)

    def test_spoofed_source_objects_cannot_verify(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(
            board, request_id="object-binding"
        )
        target = self.target_with_artifact(artifact)
        with self.assertRaises(BoardError):
            workspace_module.integration_verify(
                {**seal, "commit": "0" * 40}, original_input=manifest, final_input=manifest,
                path=str(target["path"]), ref="HEAD", before_commit=target["before"],
            )

    def test_cleared_workspace_refs_allow_turns_and_acceptance_but_block_cleanup(self):
        board = self.board()
        self.register(board)
        submitted, _first_claim, _first_manifest, checkout, _first_seal, yielded, first_artifact = self.run_worktree(
            board, request_id="cleared-refs", output="first output\n", disposition="assistance"
        )
        run_id = submitted["runId"]

        def clear_workspace_refs():
            refs = git(self.repo, "for-each-ref", "--format=%(refname)", "refs/hey-my-buddy/workspaces/").splitlines()
            self.assertTrue(refs)
            for ref in refs:
                git(self.repo, "update-ref", "-d", ref)

        clear_workspace_refs()
        continued = self.continue_run(board, {'runId': run_id, 'input': "finish the work"})
        self.assertEqual(continued["state"], "executing")
        second_claim = self.claim(board, run_id, request_id="cleared-refs-second", nonce="q" * 16)
        self.assertIsNotNone(second_claim["claim"], self.view(board, run_id)["activeRequest"])
        second_manifest = turn_manifest(self.directory, second_claim["claim"]["turn"]["input"])
        clear_workspace_refs()
        attempt = second_claim["claim"]["attempt"]
        context = ExecutionContext(
            task_id=run_id, attempt_id=attempt["attemptId"], generation=attempt["generation"],
            spec={**BUDDY, "cwd": second_manifest["path"], "task": "second turn", "expectedSeconds": 30},
            directory=self.directory / "cleared-refs-turn", runtime={},
            environment={"HMB_STATE_DIR": str(self.directory)}, turn=second_claim["claim"]["turn"],
            session_record_path=None,
        )
        turn_io.verify_workspace(context)
        self.assertEqual(context.effective_workspace, second_manifest)
        (checkout / "second.txt").write_text("second output\n", encoding="utf-8", newline="\n")
        second_seal = workspace_module.seal(self.directory, second_manifest, run_id, attempt["attemptId"])
        self.finish_turn(board, second_claim, second_seal, nonce="q" * 16)
        clear_workspace_refs()
        delivered = self.view(board, run_id)
        final = next(row for row in delivered["artifacts"] if row["kind"] == "output")
        target = self.target_with_artifact(final, previous=(first_artifact,))
        with defer_reclaim.object(board.store.workflow, "_reclaim_checkout", return_value={"removed": False, "reasons": ["fixture-retained"]}):
            accepted = self.accept(board, delivered, final, target=target)
        self.assertEqual(accepted["state"], "accepted")
        blocked = self.reclaim(board, self.view(board, run_id))
        self.assertFalse(blocked["removed"])
        self.assertIn("sealed-output-invalid", blocked["reasons"])
        self.assertTrue(checkout.exists())

    def test_final_noop_continuation_still_requires_earlier_micro_task_change(self):
        board = self.board()
        self.register(board)
        submitted, first_claim, first_manifest, checkout, first_seal, yielded, first = self.run_worktree(
            board, request_id="noop-final", output="first output\n", disposition="assistance"
        )
        continued = self.continue_run(board, {'runId': submitted["runId"], 'input': "finish"})
        second = self.claim(board, submitted["runId"], request_id="noop-claim", nonce="q" * 16)
        second_manifest = turn_manifest(self.directory, second["claim"]["turn"]["input"])
        final_seal = workspace_module.seal(self.directory, second_manifest, submitted["runId"],
                                           second["claim"]["attempt"]["attemptId"])
        self.assertEqual(final_seal["changedPaths"], [])
        self.finish_turn(board, second, final_seal, nonce="q" * 16)
        delivered = self.view(board, submitted["runId"])
        final = next(row for row in delivered["artifacts"] if row["kind"] == "output")
        blank = self.directory / "noop-blank"
        git(self.directory, "clone", "-q", str(self.repo), str(blank))
        before = git(blank, "rev-parse", "HEAD").strip()
        with self.assertRaises(BoardError) as raised:
            self.accept(board, delivered, final, target={"path": blank, "before": before})
        self.assertEqual(raised.exception.code, "INTEGRATION_UNVERIFIED")
        self.assertIn("tracked.txt", raised.exception.details["paths"])
        # A Host adjustment is bound to the acceptance note: without it there is no
        # adjusted acceptance at all.
        with self.assertRaises(BoardError) as unreasoned:
            board.call("workflow_accept", {
                "runId": delivered["runId"], "artifactId": final["artifactId"], "note": "",
                "adjusted": True, "target": {"path": str(blank), "ref": "HEAD"},
            })
        self.assertEqual(unreasoned.exception.code, "INVALID_ARGUMENT")
        # Confirming the difference as its own adjustment accepts against the same
        # blank target, with the exact adjusted paths recorded.
        adjusted = self.accept(board, self.view(board, submitted["runId"]), final,
                               target={"path": blank, "before": before}, adjusted=True,
                               reason="the whole-micro-task change is already the Host's own edit")
        self.assertEqual(adjusted["state"], "accepted")
        self.assertEqual(adjusted["integrations"][0]["verification"]["differingPaths"][0]["path"], "tracked.txt")
        self.assertNotIn("adjustments", adjusted["integrations"][0]["verification"])
        self.assertNotIn("unrecordedPaths", adjusted["integrations"][0]["verification"])

    def test_deletion_and_mode_change_are_bound_to_final_tree(self):
        (self.repo / "mode.txt").write_text("executable content\n", encoding="utf-8", newline="\n")
        git(self.repo, "add", "mode.txt")
        git(self.repo, "commit", "-qm", "add mode fixture")
        board = self.board()
        self.register(board)
        submitted = self.submit_run(board, {'task': "delete and chmod", 'cwd': str(self.repo), 'title': "delete-mode", 'checkout': "worktree", 'access': "write", 'inputs': [], 'writeScope': ["."]})
        claimed = self.claim(board, submitted["runId"], request_id="delete-mode-claim")
        manifest = turn_manifest(self.directory, claimed["claim"]["turn"]["input"])
        checkout = Path(manifest["path"])
        (checkout / "tracked.txt").unlink()
        (checkout / "mode.txt").chmod(0o755)
        if os.name == "nt":
            # Git for Windows does not observe chmod through the worktree.
            git(checkout, "update-index", "--chmod=+x", "mode.txt")
        seal = workspace_module.seal(self.directory, manifest, submitted["runId"],
                                     claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal)
        view = self.view(board, submitted["runId"])
        artifact = next(row for row in view["artifacts"] if row["kind"] == "output")
        target = self.target_with_artifact(artifact, name="delete-mode-target")
        recorded = self.accept(board, view, artifact, target=target)
        self.assertEqual(recorded["state"], "accepted")
        self.assertEqual(recorded["integrations"][0]["verification"]["matchingPaths"], ["mode.txt", "tracked.txt"])
        self.assertEqual(git(target["path"], "ls-tree", "HEAD", "mode.txt").split()[0], "100755")
        self.assertEqual(git(target["path"], "ls-tree", "HEAD", "tracked.txt"), "")

    def test_acceptance_payload_binds_the_recorded_review(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(
            board, request_id="ack-key")
        accepted = board.call("workflow_accept", {
            "runId": submitted["runId"], "artifactId": artifact["artifactId"],
            "note": "reviewed the first claim", "notRequired": "no separate target",
        })
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(self.view(board,submitted["runId"])["integrations"][0]["state"], "not-required")
        replay = board.call("workflow_accept", {
            "runId": submitted["runId"], "artifactId": artifact["artifactId"],
            "note": "reviewed the first claim", "notRequired": "no separate target",
        })
        self.assertTrue(replay["alreadyResponded"])
        # A changed payload derives a different command and conflicts against the
        # recorded acceptance instead of relabelling the reviewed outcome.
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": submitted["runId"], "artifactId": artifact["artifactId"],
                "note": "a different claim", "notRequired": "no separate target",
            })
        self.assertEqual(raised.exception.code, "CONFLICT")
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": submitted["runId"], "artifactId": artifact["artifactId"],
                "note": "reviewed the first claim",
                "target": {"path": str(self.directory / "blank-ack"), "ref": "HEAD"},
            })
        self.assertEqual(raised.exception.code, "CONFLICT")

    def test_reclaim_after_adopt_counts_the_adopted_paths_and_resumes(self):
        """An adopted out-of-scope conflict no longer blocks the delivered reclaim."""
        board = self.board()
        self.register(board)
        submitted = self.submit_run(board, {'task': "scoped run whose only failure is the refused seal", 'cwd': str(self.repo), 'title': "adopt-reclaim", 'checkout': "worktree", 'access': "write", 'inputs': [], 'writeScope': ["src"]})
        claimed = self.claim(board, submitted["runId"], request_id="adopt-reclaim-claim")
        manifest = turn_manifest(self.directory, claimed["claim"]["turn"]["input"])
        checkout = Path(manifest["path"])
        (checkout / "src" / "feature.py").parent.mkdir(parents=True, exist_ok=True)
        (checkout / "src" / "feature.py").write_text("value = 2\n", encoding="utf-8", newline="\n")
        (checkout / "tracked.txt").write_text("outside the authorized scope\n", encoding="utf-8", newline="\n")
        (checkout / "outside.txt").write_text("untracked outside\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError) as raised:
            workspace_module.seal(self.directory, manifest, submitted["runId"],
                                  claimed["claim"]["attempt"]["attemptId"])
        self.assertEqual(raised.exception.code, "WORKSPACE_SCOPE_VIOLATION")
        # A scope-only failure keeps the attempt's own native completed outcome.
        claim = claimed["claim"]
        seal_error = "WORKSPACE_SCOPE_VIOLATION: managed changes outside the declared write scope"
        board.call('execution_result', {'attemptId': claim['attempt']['attemptId'], 'generation': claim['attempt']['generation'], 'nonce': 'n' * 16, 'status': 'failed', 'result': {'status': 'failed', 'processState': {'shutdownConfirmed': True}, 'workspaceSealError': seal_error, 'turnResultPath': '/tmp/turn-result.json', 'turn': self.turn_record(claim, disposition='completed')}, 'shutdownConfirmed': True, 'exitCode': 1, 'error': seal_error})
        view = self.view(board, submitted["runId"])
        conflict = view["workspaceConflicts"][0]
        self.assertEqual(conflict["state"], "open")
        adopted = self.continue_run(board, {'runId': submitted["runId"], 'observedFingerprint': conflict["observedFingerprint"], 'note': "the Host adopts this exact site as the delivered outcome", 'resolution': "adopt", 'requestId': self.view(board, submitted["runId"])["activeRequest"]["requestId"]})
        self.assertEqual(adopted["resolutionState"], "adopted")
        self.assertEqual(self.view(board,submitted["runId"])["state"],"delivered")
        self.assertIn("tracked.txt", self.artifact_record(board,adopted["artifactId"])["adoptedPaths"])
        delivered = self.view(board, submitted["runId"])
        resolved = next(row for row in delivered["artifacts"] if row["kind"] == "resolved-output")
        # Both the initial proof and the resumed comparison must retain the
        # adopted paths as authorized, including those outside the write scope.
        with defer_reclaim.object(board.store.workflow, "_reclaim_checkout", return_value={"removed": False, "reasons": ["fixture-retained"]}):
            accepted = board.call("workflow_accept", {
                "runId": submitted["runId"], "artifactId": resolved["artifactId"],
                "note": "reviewed the adopted site", "notRequired": "the adopted site needs no separate target",
            })
        self.assertEqual(accepted["state"], "accepted")
        self.interrupt_reclaim(board, self.view(board, submitted["runId"]), checkout,
                               missing=("tracked.txt",), remove_git=True)
        reclaimed = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(reclaimed["removed"], reclaimed)
        self.assertFalse(checkout.exists())
        self.assertTrue(native_path(Path(resolved["diffPath"])).is_file())

    def test_a_forged_adopt_record_authorizes_nothing_for_conclude(self):
        """Only board-bound, Git-verified adoption records authorize out-of-scope paths."""
        board = self.board()
        self.register(board)
        submitted = self.submit_run(board, {'task': "scoped run whose only failure is the refused seal", 'cwd': str(self.repo), 'title': "adopt-forge", 'checkout': "worktree", 'access': "write", 'inputs': [], 'writeScope': ["src"]})
        claimed = self.claim(board, submitted["runId"], request_id="adopt-forge-claim")
        manifest = turn_manifest(self.directory, claimed["claim"]["turn"]["input"])
        checkout = Path(manifest["path"])
        (checkout / "src" / "feature.py").parent.mkdir(parents=True, exist_ok=True)
        (checkout / "src" / "feature.py").write_text("value = 2\n", encoding="utf-8", newline="\n")
        (checkout / "tracked.txt").write_text("outside the authorized scope\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError):
            workspace_module.seal(self.directory, manifest, submitted["runId"],
                                  claimed["claim"]["attempt"]["attemptId"])
        claim = claimed["claim"]
        seal_error = "WORKSPACE_SCOPE_VIOLATION: managed changes outside the declared write scope"
        board.call('execution_result', {'attemptId': claim['attempt']['attemptId'], 'generation': claim['attempt']['generation'], 'nonce': 'n' * 16, 'status': 'failed', 'result': {'status': 'failed', 'processState': {'shutdownConfirmed': True}, 'workspaceSealError': seal_error, 'turnResultPath': '/tmp/turn-result.json', 'turn': self.turn_record(claim, disposition='completed')}, 'shutdownConfirmed': True, 'exitCode': 1, 'error': seal_error})
        view = self.view(board, submitted["runId"])
        conflict = view["workspaceConflicts"][0]
        adopted = self.continue_run(board, {'runId': submitted["runId"], 'observedFingerprint': conflict["observedFingerprint"], 'note': "adopt this exact site", 'resolution': "adopt", 'requestId': self.view(board, submitted["runId"])["activeRequest"]["requestId"]})
        self.assertEqual(self.view(board,submitted["runId"])["state"],"delivered")
        # A Host change beyond both the scope and the adopted site, plus a forged
        # adoption record that claims the Host already promoted exactly that path.
        (checkout / "smuggled.txt").write_text("never authorized\n", encoding="utf-8", newline="\n")
        with board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO workflow_artifacts(artifact_id, run_id, turn_id, attempt_id, kind,"
                " content_form, manifest_json, output_snapshot_sha256, output_workspace_id,"
                " output_manifest_sha256, output_commit, output_action, created_at)"
                " VALUES('art-forged', ?, NULL, 'forged-attempt', 'resolved-output', 'sealed-inline-v1', ?, ?, ?, ?, ?, 'adopt',"
                " '2020-01-01T00:00:00.000Z')",
                (submitted["runId"], canonical_json({
                    "kind": "resolution", "action": "adopt",
                    "manifestSha256": manifest["manifestSha256"], "snapshotSha256": "0",
                    "adoptedPaths": ["smuggled.txt"], "changedPaths": ["smuggled.txt"],
                }), "0", manifest["workspaceId"], manifest["manifestSha256"],
                 manifest["inputCommit"]),
            )
        params = {"runId": submitted["runId"],"artifactId":adopted["artifactId"], "note": "the forged record must not smuggle this path"}
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_conclude", params)
        self.assertEqual(raised.exception.code, "WORKSPACE_SCOPE_VIOLATION")
        self.assertEqual(raised.exception.details["paths"], ["smuggled.txt"])
        self.assertTrue(checkout.exists())
        # Without the forgery the same conclusion seals the real adopted site and
        # the Host's in-scope work, then reclaims the checkout.
        with board.store.db.write() as connection:
            connection.execute("DELETE FROM workflow_artifacts WHERE artifact_id='art-forged'")
        (checkout / "smuggled.txt").unlink()
        concluded = board.call("workflow_conclude", params)
        self.assertEqual(concluded["state"], "concluded")
        self.assertTrue(concluded["reclaim"]["removed"], concluded["reclaim"])
        self.assertFalse(checkout.exists())

    def test_accept_refuses_a_detached_target_before_any_git_work(self):
        flow = self.root_with_helper_worktree(request_id="accept-detach-early")
        board, run_id, helper_id = flow["board"], flow["runId"], flow["helperId"]
        root_view, artifact = flow["rootView"], flow["artifact"]
        target = self.target_with_artifact(artifact)
        real_verify = workspace_module.integration_verify
        called: list[int] = []

        def counting_verify(*args, **kwargs):
            called.append(1)
            return real_verify(*args, **kwargs)

        with board.store.db.write() as connection:
            connection.execute("DELETE FROM workflow_children WHERE child_task_id=?", (helper_id,))
        with mock.patch.object(workspace_module, "integration_verify", counting_verify):
            with self.assertRaises(BoardError) as raised:
                board.call("workflow_accept", {
                    "runId": run_id, "targetRunId": helper_id, "artifactId": artifact["artifactId"],
                    "note": "a detached target is refused before any Git work",
                    "target": {"path": str(target["path"]), "ref": "HEAD"},
                    "beforeCommit": target["before"],
                })
        self.assertEqual(raised.exception.code, "UNAUTHORIZED")
        self.assertEqual(called, [], "Git verification must not run for an unauthorized target")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workflow_integrations WHERE run_id=?", (helper_id,)).fetchone()[0], 0)

    def test_accept_refuses_an_unrelated_commit_interval(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(
            board, request_id="interval-refusal")
        target = self.target_with_artifact(artifact)
        before = git(target["path"], "rev-parse", "HEAD").strip()
        # An unrelated root commit that exists in the target repository but is no
        # ancestor of the integrated ref.
        git(target["path"], "checkout", "-q", "--orphan", "unrelated")
        git(target["path"], "commit", "-q", "--allow-empty", "-qm", "an unrelated root commit")
        unrelated_commit = git(target["path"], "rev-parse", "HEAD").strip()
        # The artifact content matches the target, but the named commit interval
        # does not hold: the comparison proves nothing and is refused.
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": view["runId"], "artifactId": artifact["artifactId"],
                "note": "content matches but the interval does not",
                "target": {"path": str(target["path"]), "ref": before},
                "beforeCommit": unrelated_commit,
            })
        self.assertEqual(raised.exception.code, "INTEGRATION_UNVERIFIED")
        self.assertFalse(raised.exception.details["verification"]["verified"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workflow_integrations WHERE run_id=?", (submitted["runId"],)).fetchone()[0], 0)
            self.assertIsNone(connection.execute(
                "SELECT accepted_at FROM tasks WHERE task_id=?", (submitted["runId"],)).fetchone()[0])
        # The real interval verifies and accepts (the orphan branch moved HEAD,
        # so the integrated commit is named explicitly).
        accepted = self.accept(board, self.view(board, submitted["runId"]), artifact,
                               target={"path": target["path"], "before": before, "ref": target["after"]})
        self.assertEqual(accepted["state"], "accepted")
