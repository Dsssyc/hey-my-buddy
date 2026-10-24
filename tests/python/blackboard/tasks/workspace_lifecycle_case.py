"""The shared lifecycle fixture: a real Git template and the turn helpers.

Every lifecycle suite needs the same private board, the same initial repository
template and the same submit/claim/seal/finish/accept/reclaim helpers; this
module holds them once so each themed suite carries only its own tests.
"""
from __future__ import annotations
from admission import admit_board
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from support import BoardTestCase
from protocol.fixtures import native_turn
from blackboard.tasks.test_workflow_real import BUDDY, GIT_ENV, RealWorkspaceTestCase, private_environment
import blackboard.tasks.test_workspace_identity as identity_fixtures
from hey_my_buddy import private_dirs
from hey_my_buddy.blackboard.tasks import workflow as workflow_module
from hey_my_buddy.blackboard.tasks import workspace as workspace_module
from hey_my_buddy.blackboard.store.db import canonical_json
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import transport

def git(path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *arguments], env={**os.environ, **GIT_ENV}, capture_output=True, text=True, encoding="utf-8",
    )
    if completed.returncode != 0:
        raise AssertionError(f"git {' '.join(arguments)} failed: {completed.stderr}")
    return completed.stdout


class LifecycleTestCase(RealWorkspaceTestCase):
    fixed_files = identity_fixtures.WorkspaceIdentityFixture.fixed_files

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        temporary = tempfile.TemporaryDirectory(prefix="hey-my-buddy-lifecycle-repository-")
        cls.addClassCleanup(temporary.cleanup)
        fixture = RealWorkspaceTestCase(methodName="runTest")
        try:
            # Build the same initial repository through the existing fixture,
            # then finish all its ordinary private cleanups before any method.
            fixture.setUp()
            cls._repository_template = Path(temporary.name) / "repo"
            shutil.copytree(fixture.repo, cls._repository_template)
        finally:
            if not fixture.doCleanups():
                raise AssertionError("Initial repository fixture cleanup failed")

    def setUp(self) -> None:
        # Keep the base fixture's private board/environment/catalog lifecycle;
        # only its invariant initial Git construction moves to setUpClass.
        BoardTestCase.setUp(self)
        private_environment(self)
        self.catalog_fixture()
        self.repo = self.directory / "repo"
        shutil.copytree(self._repository_template, self.repo)
        self.git("update-index", "--refresh")
        self.controls: dict[str, dict] = {}
        self.worker = "w-life"
        self._previous_workspace = workflow_module._workspace_module
        workflow_module._workspace_module = workspace_module
        self.addCleanup(self._restore_workspace)

    def _restore_workspace(self) -> None:
        workflow_module._workspace_module = self._previous_workspace

    # -- micro-task helpers ----------------------------------------------------
    def register(self, board, worker_id=None):
        return None

    def control(self, view):
        return dict(self.controls[view["runId"]])

    def claim(self, board, run_id, *, request_id="claim-1", nonce="n" * 16):
        return admit_board(board, {'workerId': self.worker, 'claimRequestId': request_id, 'nonce': nonce, 'runId': run_id})

    def turn_record(self, claim, *, disposition="completed"):
        """A synthetic micro-task turn record in the current provenance format.

        The record passes the board's real import validator; no harness ran, so
        it is not real native verification (``protocol.fixtures.native_turn``).
        """
        outcome = {"disposition": disposition, "summary": "did the work", "remaining": [], "decisions": [],
                   "artifacts": [], "request": None}
        if disposition in ("assistance", "attention"):
            outcome["request"] = {"summary": "please review", "attempted": "tried the obvious fix",
                                  "neededWork": ["decide the schema"], "expectedArtifacts": ["a reviewed design"],
                                  "acceptance": "the design is approved"}
        return native_turn.claim_record(claim, outcome=outcome)

    def finish_turn(self, board, claimed, seal, *, disposition="completed", nonce="n" * 16, worker_id=None):
        claim = claimed["claim"]
        attempt = claim["attempt"]
        return board.call('execution_result', {'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': nonce, 'status': 'ok', 'terminationReason': 'completed', 'result': {'status': 'ok', 'processState': {'shutdownConfirmed': True}, 'turn': self.turn_record(claim, disposition=disposition), 'turnResultPath': '/tmp/turn-result.json', 'workspaceSeal': seal}, 'shutdownConfirmed': True, 'exitCode': 0})

    def view(self, board, run_id, **extra):
        return board.call("workflow_get", {"runId": run_id, **extra})

    def run_worktree(self, board, *, request_id="lifecycle-1", output="sealed output\n", disposition="completed"):
        submitted = board.call("workflow_submit", {
            **BUDDY, "requestId": request_id, "hostId": "host-1", "task": "isolated worktree task",
            "cwd": str(self.repo), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]},
        })
        self.controls[submitted["runId"]] = submitted["control"]
        claimed = self.claim(board, submitted["runId"], request_id=f"{request_id}-claim")
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
        checkout = Path(manifest["path"])
        (checkout / "tracked.txt").write_text(output, encoding="utf-8", newline="\n")
        seal = workspace_module.seal(self.directory, manifest, submitted["runId"],
                                     claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal, disposition=disposition)
        view = self.view(board, submitted["runId"])
        artifact = next(row for row in view["artifacts"] if row["kind"] == "output")
        return submitted, claimed, manifest, checkout, seal, view, artifact

    def accept(self, board, view, artifact, *, not_required=False, target=None, reason=None,
               note="inspected the sealed output", **extra):
        """One-step acceptance: the service derives and verifies the integration itself."""
        params = {
            "runId": view["runId"], "artifactId": artifact["artifactId"], "note": note,
            **self.control(view),
        }
        if not_required:
            params["notRequired"] = "the artifact needs no repository target" if reason is None else reason
        else:
            self.assertIsNotNone(target)
            target_params = {"path": str(target["path"]), "ref": target.get("ref") or "HEAD"}
            for key in ("repositoryId", "checkoutId"):
                if target.get(key):
                    target_params[key] = target[key]
            params["target"] = target_params
            if target.get("before"):
                params["beforeCommit"] = target["before"]
        params.update(extra)
        return board.call("workflow_accept", params)

    def reclaim(self, board, view, **extra):
        params = {"runId": view["runId"], **self.control(view)}
        params.update(extra)
        try:
            return board.call("workflow_reclaim", params)
        except BoardError as error:
            error.add_note(repr(error.details))
            raise

    def target_with_artifact(self, artifact, *, name="target", previous=()):
        target = self.directory / name
        git(self.directory, "clone", "-q", str(self.repo), str(target))
        before = git(target, "rev-parse", "HEAD").strip()
        for item in (*previous, artifact):
            completed = subprocess.run(
                ["git", "-C", str(target), "apply", "--binary", item["diffPath"]],
                env={**os.environ, **GIT_ENV}, capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        git(target, "add", ".")
        git(target, "commit", "-qm", "integrate")
        after = git(target, "rev-parse", "HEAD").strip()
        return {"path": target, "before": before, "after": after}

    def acknowledge(self, board, view, artifact, *, verdict="accepted", **extra):
        """Compatibility shim: every acknowledgement is one accept under the current contract."""
        assert verdict == "accepted"
        return self.accept(board, view, artifact, **extra)

    def rename_lifecycle(self, *, long_paths=False, historical=False):
        file_count = 3000 if long_paths else 1000
        prefix = Path(*(["x" * 220] * 3)) if long_paths else Path()
        (self.repo / prefix).mkdir(parents=True, exist_ok=True)

        def name(kind, number):
            return prefix / f"{kind}-{number:04d}.txt"

        for number in range(file_count):
            (self.repo / name("old", number)).write_text(f"file {number}\n", encoding="utf-8", newline="\n")
        self.git("add", ".")
        self.git("commit", "-qm", "rename input")
        original = self.git("rev-parse", "HEAD").strip()
        board = self.board()
        submitted = board.call("workflow_submit", {
            **BUDDY, "requestId": "thousand-renames", "hostId": "host-1", "task": "rename files",
            "cwd": str(self.repo), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]},
        })
        run_id = submitted["runId"]
        self.controls[run_id] = submitted["control"]
        claimed = self.claim(board, run_id)
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
        checkout = Path(manifest["path"])
        for number in range(file_count):
            (checkout / name("old", number)).rename(checkout / name("new", number))
        sealed = workspace_module.seal(self.directory, manifest, run_id,
                                       claimed["claim"]["attempt"]["attemptId"])
        expected = sorted(str(name(kind, number)).replace(os.sep, "/")
                          for kind in ("new", "old") for number in range(file_count))
        self.assertEqual(sealed["changedPaths"], expected)
        self.assertEqual(sealed["snapshot"]["version"], 1)
        self.assertNotIn("changedEntries", sealed["snapshot"])
        self.assertNotIn("changedEntriesTruncated", sealed["snapshot"])
        historical_bytes = {}
        if historical:
            changes = workspace_module._tree_changes(checkout, manifest["inputTree"], sealed["tree"])
            sealed["snapshot"].pop("version")
            sealed["snapshot"]["changedEntries"] = {
                path: [changes[path]["mode"], changes[path]["oid"]] if changes[path] is not None else None
                for path in expected[:512]}
            sealed["snapshot"]["changedEntriesTruncated"] = True
            sealed["snapshotSha256"] = workspace_module._sha(workspace_module._json(sealed["snapshot"]))
            for record_name in ("pending.json", "output.json"):
                record_path = Path(sealed["diffPath"]).with_name(record_name)
                record_path.write_bytes(workspace_module._json(sealed))
                historical_bytes[record_path] = record_path.read_bytes()
        # Exercise both the sealed first-turn patch and the whole-task Git diff.
        for base in (manifest["inputCommit"], original):
            cumulative = workspace_module.cumulative_patch(manifest, sealed, base)
            self.assertEqual(cumulative["changedPaths"], expected)
            self.assertEqual(Path(cumulative["path"]).read_bytes(), Path(sealed["diffPath"]).read_bytes())
        sealed["cumulativePatch"] = cumulative
        report = workspace_module.integration_verify(
            sealed, original_input=manifest, final_input=manifest, path=str(self.repo), ref="HEAD",
            before_commit=original,
        )
        self.assertFalse(report["verified"])
        self.assertEqual(len(report["differingPaths"]), file_count)
        self.assertEqual(len(report["missingPaths"]), file_count)
        self.assertEqual(len(report["unrecordedPaths"]), 2 * file_count)
        receipt_sizes = []
        execute_result = board.service.execution_result

        def record_receipt_size(frame):
            receipt_sizes.append(Path(json.loads(frame)["receipt"]["path"]).stat().st_size)
            return execute_result(frame)

        with mock.patch.object(board.service, "execution_result", side_effect=record_receipt_size) as called:
            self.finish_turn(board, claimed, sealed)
        wire = called.call_args.args[0]
        self.assertLess(len(wire.encode()), 2048)
        if long_paths:
            self.assertGreater(receipt_sizes[0], 8 * 1024 * 1024)
        with board.store.db.read() as connection:
            stored_result = json.loads(connection.execute("SELECT result_json FROM attempts WHERE attempt_id=?",
                                       (claimed["claim"]["attempt"]["attemptId"],)).fetchone()[0])
        self.assertEqual(stored_result["result"]["workspaceSeal"], sealed)
        view = self.view(board, run_id)
        artifact = next(row for row in view["artifacts"] if row["kind"] == "output")
        target = self.target_with_artifact(artifact)
        accepted = self.accept(board, view, artifact, target=target, keepCheckout=True)
        self.assertEqual(accepted["state"], "accepted")
        verification = accepted["integration"]["verification"]
        self.assertEqual(verification["matchingPaths"], [path[:500] for path in expected[:32]])
        self.assertEqual(verification["matchingPathsCount"], len(expected))
        self.assertTrue(verification["matchingPathsTruncated"])
        self.assertEqual(artifact["changedPathsCount"], len(expected))
        self.assertEqual(artifact["changedPaths"], [path[:500] for path in expected[:32]])
        self.assertLess(len(canonical_json(accepted).encode()), 8 * 1024 * 1024)
        with board.store.db.read() as connection:
            stored = connection.execute("SELECT verification_json FROM workflow_integrations WHERE integration_id=?",
                                        (accepted["integration"]["integrationId"],)).fetchone()[0]
        self.assertEqual(set(json.loads(stored)),
                         {"matchingPaths", "differingPaths", "missingPaths", "hostPaths", "note"})
        self.assertEqual(json.loads(stored)["matchingPaths"], expected)
        self.assertNotIn("verified", verification)
        self.assertNotIn("summary", verification)
        self.assertTrue(checkout.exists())
        for number in range(file_count):
            (checkout / name("new", number)).write_text("unsealed\n", encoding="utf-8", newline="\n")
        cleanup = workspace_module.cleanup_inspect(self.directory, manifest, sealed=sealed)
        self.assertFalse(cleanup["eligible"])
        self.assertEqual(cleanup["unsealedPaths"], sorted(str(name("new", number)).replace(os.sep, "/")
                                                       for number in range(file_count)))
        for number in range(file_count):
            (checkout / name("new", number)).write_text(f"file {number}\n", encoding="utf-8", newline="\n")
        self.assertTrue(self.reclaim(board, self.view(board, run_id))["removed"])
        self.assertFalse(checkout.exists())
        for path, original_bytes in historical_bytes.items():
            self.assertEqual(path.read_bytes(), original_bytes)

    # -- cleanup -------------------------------------------------------------
    def accepted_worktree(self, *, request_id="cleanup-1"):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(board, request_id=request_id)
        target = self.target_with_artifact(artifact)
        accepted = self.accept(board, view, artifact, target=target, reason="verified before cleanup",
                               keepCheckout=True)
        self.assertEqual(accepted["state"], "accepted")
        return board, submitted, manifest, checkout, artifact, self.view(board, submitted["runId"])

    # -- continuation cleanup -------------------------------------------------
    def two_turn_worktree(self, *, request_id="continuation-cleanup"):
        """One managed worktree whose second stage is prepared from the first seal.

        The continuation reuses the same physical checkout under a new logical
        workspace id, so the current run manifest is no longer the worktree
        manifest that created the allocation.
        """
        board = self.board()
        self.register(board)
        submitted, first_claim, first_manifest, checkout, first_seal, yielded, first_artifact = self.run_worktree(
            board, request_id=request_id, output="first turn output\n", disposition="assistance"
        )
        run_id = submitted["runId"]
        self.assertEqual(yielded["state"], "awaiting-host")
        continued = board.call("workflow_continue", {
            "runId": run_id, "commandId": f"{request_id}-continue", "expectedRevision": yielded["revision"],
            "input": "finish the micro task", **self.control(yielded),
        })
        self.assertEqual(continued["state"], "executing")
        second = self.claim(board, run_id, request_id=f"{request_id}-claim-2", nonce="q" * 16)
        self.assertIsNotNone(second["claim"], second)
        second_manifest = second["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(second_manifest["kind"], "existing")
        self.assertNotEqual(second_manifest["workspaceId"], first_manifest["workspaceId"])
        for field in ("checkoutRoot", "checkoutId", "repositoryId", "path"):
            self.assertEqual(second_manifest[field], first_manifest[field])
        # A new-file second stage keeps the sealed patch applicable to a fresh target.
        (checkout / "second.txt").write_text("second turn output\n", encoding="utf-8", newline="\n")
        second_seal = workspace_module.seal(self.directory, second_manifest, run_id,
                                            second["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, second, second_seal, nonce="q" * 16)
        delivered = self.view(board, run_id)
        self.assertEqual(delivered["state"], "delivered")
        final = next(row for row in delivered["artifacts"] if row["kind"] == "output")
        target = self.target_with_artifact(final, previous=(first_artifact,))
        accepted = self.accept(board, delivered, final, target=target,
                               reason="verified the sealed second stage", keepCheckout=True)
        self.assertEqual(accepted["state"], "accepted")
        return board, submitted, first_manifest, second_manifest, final, self.view(board, run_id)

    def failed_evidence_worktree(self, *, import_failure=False):
        board = self.board()
        self.register(board)
        submitted = board.call('workflow_submit', {
            **BUDDY, 'requestId': 'failed-evidence', 'hostId': 'host-1',
            'task': 'retry after an invalid native result', 'cwd': str(self.repo),
            'submissionToken': 't' * 32,
            'executionWorkspace': {'kind': 'worktree', 'access': 'write', 'writeScope': ['.']},
        })
        run_id = submitted['runId']
        self.controls[run_id] = submitted['control']
        first = self.claim(board, run_id, request_id='failed-evidence-first')['claim']
        attempt = first['attempt']
        manifest = first['turn']['input']['executionWorkspace']
        checkout = Path(manifest['path'])
        failed_root = private_dirs.ensure_private_dir(
            private_dirs.attempt_root(board.directory, 'dsh', run_id, attempt['attemptId']))
        credential = failed_root / 'agent-credential.json'
        credential.write_text('private credential fixture', encoding="utf-8", newline="\n")
        refs = [str(failed_root / name) for name in ('unknown-events.json', 'thread-notifications.json')]
        for ref in refs:
            Path(ref).write_text('failure evidence', encoding="utf-8", newline="\n")
        native = private_dirs.ensure_private_dir(private_dirs.native_root(board.directory, 'dsh', run_id))
        (native / 'session.json').write_text('native continuation state', encoding="utf-8", newline="\n")
        seal = workspace_module.seal(self.directory, manifest, run_id, attempt['attemptId'])
        result = {'status': 'ok' if import_failure else 'invalid-result', 'evidenceRefs': refs,
                  'workspaceSeal': seal, 'processState': {'shutdownConfirmed': True}}
        if import_failure:
            result['turn'] = {'invalid': 'not an importable turn'}
        board.call('execution_result', {'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': 'n' * 16, 'status': 'ok' if import_failure else 'failed', 'terminationReason': 'completed' if import_failure else 'harness-error', 'shutdownConfirmed': True, 'exitCode': 1, 'result': result})
        # This in-process fixture has no execution process; supply the same
        # creator stop proof that enters the service-owned cleanup path.
        board.store.recover_executions(attempt['attemptId'],
                                      stopped_evidence={'kind': 'creator-reaped', 'exitCode': 1})
        self.assertFalse(credential.exists())
        receipt = board.call('task_result', {'runId': run_id})
        recorded = receipt['result']
        self.assertEqual(recorded['evidenceRefs'], refs)
        self.assertEqual(receipt['resultMeta']['status'], 'failed')
        if import_failure:
            self.assertEqual(receipt['resultMeta']['terminationReason'], 'completed')
            self.assertIn('microTaskError', recorded)
        with board.store.db.read() as connection:
            recorded = json.loads(connection.execute('SELECT result_json FROM attempts WHERE attempt_id=?',
                                   (attempt['attemptId'],)).fetchone()['result_json'])['result']
        return board, run_id, attempt, checkout, failed_root, refs, native, recorded

    def assert_failed_evidence_survives_continuation(self, *, import_failure=False):
        board, run_id, attempt, checkout, failed_root, refs, native, recorded = self.failed_evidence_worktree(
            import_failure=import_failure)
        failed = self.view(board, run_id)
        board.call('workflow_continue', {
            'runId': run_id, 'commandId': 'retry-failed-evidence', 'expectedRevision': failed['revision'],
            'input': 'finish with a valid result', **self.control(failed),
        })
        second = self.claim(board, run_id, request_id='failed-evidence-second', nonce='q' * 16)
        second_id = second['claim']['attempt']['attemptId']
        second_manifest = second['claim']['turn']['input']['executionWorkspace']
        successful_root = private_dirs.ensure_private_dir(
            private_dirs.attempt_root(board.directory, 'dsh', run_id, second_id))
        (successful_root / 'thread-notifications.json').write_text('successful evidence', encoding="utf-8", newline="\n")
        (checkout / 'tracked.txt').write_text('completed after retry\n', encoding="utf-8", newline="\n")
        second_seal = workspace_module.seal(self.directory, second_manifest, run_id, second_id)
        self.finish_turn(board, second, second_seal, nonce='q' * 16)
        delivered = self.view(board, run_id)
        artifact = next(row for row in delivered['artifacts'] if row['kind'] == 'output')
        target = self.target_with_artifact(artifact)
        credential = failed_root / 'agent-credential.json'
        accepted = self.accept(board, delivered, artifact, target=target)
        self.assertEqual(accepted['state'], 'accepted')
        self.assertFalse(checkout.exists())
        self.assertFalse(native.exists())
        self.assertFalse(successful_root.exists())
        self.assertFalse(credential.exists())
        self.assertEqual([Path(ref).read_text(encoding="utf-8") for ref in recorded['evidenceRefs']], ['failure evidence'] * 2)
        with board.store.db.write() as connection:
            stored = json.loads(connection.execute('SELECT result_json FROM attempts WHERE attempt_id=?',
                                                   (attempt['attemptId'],)).fetchone()['result_json'])
            self.assertEqual(stored['result'], recorded)
            connection.execute("UPDATE tasks SET accepted_at='2000-01-01T00:00:00Z' WHERE task_id=?", (run_id,))
        self.assert_storage_reclaims_evidence(board, run_id, failed_root.parent.parent)

    def assert_storage_reclaims_evidence(self, board, run_id, root):
        board.console.close()
        board.store.db.close()
        # Exercise the existing inventory and guarded deletion through a real private daemon.
        with self.daemon():
            endpoint = transport._read_endpoint(self.directory)
            service = SimpleNamespace(call=lambda operation, params: transport._request(
                endpoint, operation, params, state_dir=self.directory))
            planned = service.call('storage_plan', {})
            candidate = next(row for row in planned['candidates'] if row['path'] == str(root))
            self.assertEqual(candidate['runId'], run_id)
            self.assertEqual(candidate['reasons'], [])
            self.assertTrue(candidate['eligible'], candidate)
            result = service.call('storage_apply', {
                'planId': planned['planId'], 'commandId': 'remove-failed-evidence', 'confirm': True,
            })
            self.assertTrue(result['complete'], result)
            self.assertTrue(any(row['path'] == str(root) for row in result['removed']), result)
            self.assertFalse(root.exists())
            after = service.call('storage_plan', {})
            self.assertFalse(any(row['path'] == str(root) for row in after['candidates']))

    # -- Host control over an independently allocated helper ------------------
    def root_with_helper_worktree(self, *, request_id="helper-target"):
        """A root worktree run plus a helper that owns a separate isolated worktree.

        Only the root control capability is kept, exactly like the real Host: the
        helper's own control verifier is never issued to any caller.
        """
        board = self.board()
        self.register(board)
        submitted, first_claim, manifest, checkout, seal, yielded, artifact = self.run_worktree(
            board, request_id=request_id, output="root output\n", disposition="assistance"
        )
        run_id = submitted["runId"]
        decided = board.call("workflow_decide", {
            "runId": run_id, "requestId": yielded["activeRequest"]["requestId"],
            "commandId": f"{request_id}-decide", "expectedRevision": yielded["revision"], "decision": "approve",
            "helpers": [{**BUDDY, "requestId": f"{request_id}-helper",
                         "task": "helper owns its own worktree", "cwd": str(self.repo),
                         "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]}}],
            **self.control(yielded),
        })
        self.assertEqual(decided["decision"], "approve")
        helper_id = decided["children"][0]["taskId"]
        with board.store.db.read() as connection:
            helper_manifest = json.loads(connection.execute(
                "SELECT workspace_manifest_json FROM workflow_children WHERE child_task_id=?", (helper_id,)
            ).fetchone()["workspace_manifest_json"])
        self.assertEqual(helper_manifest["kind"], "worktree")
        self.assertNotEqual(helper_manifest["checkoutId"], manifest["checkoutId"])
        helper_claim = self.claim(board, helper_id, request_id=f"{request_id}-helper-claim", nonce="h" * 16)
        self.assertIsNotNone(helper_claim["claim"], helper_claim)
        helper_turn_manifest = helper_claim["claim"]["turn"]["input"]["executionWorkspace"]
        helper_checkout = Path(helper_turn_manifest["path"])
        (helper_checkout / "helper.txt").write_text("helper output\n", encoding="utf-8", newline="\n")
        helper_seal = workspace_module.seal(self.directory, helper_turn_manifest, helper_id,
                                            helper_claim["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, helper_claim, helper_seal, nonce="h" * 16)
        helper_view = self.view(board, helper_id)
        self.assertEqual(helper_view["state"], "delivered")
        helper_artifact = next(row for row in helper_view["artifacts"] if row["kind"] == "output")
        return {
            "board": board, "runId": run_id, "rootView": self.view(board, run_id), "manifest": manifest,
            "checkout": checkout, "helperId": helper_id, "helperView": helper_view,
            "helperManifest": helper_manifest, "helperCheckout": helper_checkout, "artifact": helper_artifact,
        }

    def helper_scope_failure(self, *, request_id="helper-scope"):
        """A root run plus a helper whose own worktree seal fails outside its scope."""
        board = self.board()
        self.register(board)
        submitted, first_claim, manifest, checkout, seal, yielded, artifact = self.run_worktree(
            board, request_id=request_id, output="root output\n", disposition="assistance"
        )
        run_id = submitted["runId"]
        decided = board.call("workflow_decide", {
            "runId": run_id, "requestId": yielded["activeRequest"]["requestId"],
            "commandId": f"{request_id}-decide", "expectedRevision": yielded["revision"], "decision": "approve",
            "helpers": [{**BUDDY, "requestId": f"{request_id}-helper", "task": "scoped helper",
                         "cwd": str(self.repo),
                         "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["src"]}}],
            **self.control(yielded),
        })
        helper_id = decided["children"][0]["taskId"]
        helper_claim = self.claim(board, helper_id, request_id=f"{request_id}-helper-claim", nonce="s" * 16)
        self.assertIsNotNone(helper_claim["claim"], helper_claim)
        helper_manifest = helper_claim["claim"]["turn"]["input"]["executionWorkspace"]
        helper_checkout = Path(helper_manifest["path"])
        (helper_checkout / "src" / "feature.py").write_text("value = 2\n", encoding="utf-8", newline="\n")
        (helper_checkout / "tracked.txt").write_text("outside the helper scope\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError) as raised:
            workspace_module.seal(self.directory, helper_manifest, helper_id,
                                  helper_claim["claim"]["attempt"]["attemptId"])
        self.assertEqual(raised.exception.code, "WORKSPACE_SCOPE_VIOLATION")
        attempt = helper_claim["claim"]["attempt"]
        seal_error = "WORKSPACE_SCOPE_VIOLATION: managed changes outside the declared write scope"
        board.call('execution_result', {'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': 's' * 16, 'status': 'failed', 'result': {'status': 'failed', 'workspaceSealError': seal_error, 'turnResultPath': '/tmp/turn-result.json'}, 'shutdownConfirmed': True, 'exitCode': 1, 'error': seal_error})
        helper_view = self.view(board, helper_id)
        self.assertEqual(helper_view["state"], "awaiting-host")
        conflict = helper_view["workspaceConflicts"][0]
        self.assertEqual(conflict["state"], "open")
        return {"board": board, "runId": run_id, "helperId": helper_id, "helperManifest": helper_manifest,
                "helperCheckout": helper_checkout, "conflict": conflict}

    def interrupted_reclaim(self, *, missing=(), remove_git=False):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(
            request_id="interrupted-reclaim")
        self.interrupt_reclaim(board, view, checkout, missing=missing, remove_git=remove_git)
        return board, submitted, manifest, checkout, artifact

    def interrupt_reclaim(self, board, view, checkout, *, missing=(), remove_git=False):
        real_git = workspace_module._git

        def interrupted_git(root, *args, **kwargs):
            if args[:2] == ("worktree", "remove"):
                for path in missing:
                    (checkout / path).unlink()
                if remove_git:
                    (checkout / ".git").unlink()
                raise RuntimeError("lost after unlock, during removal")
            return real_git(root, *args, **kwargs)

        with mock.patch.object(workspace_module, "_git", side_effect=interrupted_git):
            with self.assertRaises(RuntimeError):
                self.reclaim(board, view)
        self.assertEqual(self.view(board, view["runId"])["cleanup"]["state"], "applying")
        self.assertNotIn("locked", workspace_module._worktree_record(self.repo, checkout))

    def assert_started_reclaim_refused(self, board, run_id, checkout, reason, *, paths=None, restored=()):
        with board.store.db.read() as connection:
            before = dict(connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE run_id=? ORDER BY rowid DESC LIMIT 1",
                (run_id,),
            ).fetchone())
        self.assertEqual(before["state"], "applying")
        contents = {path.relative_to(checkout): path.read_bytes()
                    for path in checkout.rglob("*") if path.is_file()}
        with mock.patch.object(workspace_module, "cleanup_remove", side_effect=AssertionError("refused deletion")):
            with self.assertRaises(BoardError) as refused:
                self.reclaim(board, self.view(board, run_id))
        self.assertEqual(refused.exception.code, "NOT_READY")
        self.assertEqual(refused.exception.details["reasons"], [reason])
        self.assertEqual(refused.exception.details["reclaim"]["path"], str(checkout))
        if paths is not None:
            self.assertEqual(refused.exception.details["reclaim"]["unsealedPaths"], paths)
        after_contents = {path.relative_to(checkout): path.read_bytes()
                          for path in checkout.rglob("*") if path.is_file()}
        self.assertEqual({path: after_contents[path] for path in contents}, contents)
        self.assertEqual(set(after_contents) - set(contents), {Path(path) for path in restored})
        with board.store.db.read() as connection:
            after = dict(connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE plan_id=?", (before["plan_id"],),
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM events WHERE task_id=? AND kind='workflow.cleanup_applied'", (run_id,),
            ).fetchone()[0], 0)
        self.assertEqual(after, before)
        with self.assertRaises(BoardError) as fenced:
            board.store.workflow._cleanup_fence(str(checkout))
        self.assertEqual(fenced.exception.code, "PREPARATION_CONFLICT")
        self.assertEqual(fenced.exception.details["planId"], before["plan_id"])

    # -- an attached branch blocks removal with its honest diagnosis ----------
    def branch_ref(self, name):
        return git(self.repo, "for-each-ref", f"refs/heads/{name}", "--format=%(objectname)").strip()
