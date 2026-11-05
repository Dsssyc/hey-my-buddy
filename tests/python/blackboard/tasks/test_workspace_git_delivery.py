"""Git delivery keeps private snapshots out of history and verifies actual merges."""
import json
from pathlib import Path
import runpy
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from blackboard.tasks import test_workspace_review as review_fixture
from blackboard.tasks.workspace_lifecycle_case import LifecycleTestCase, git
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.routing.statistics import samples_for_run
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.workspace_manifest import artifact_manifest, record_reference, turn_manifest


class GitDeliveryTests(unittest.TestCase):
    setUp = review_fixture.WorkspaceReviewTests.setUp
    git = review_fixture.WorkspaceReviewTests.git
    intent = review_fixture.WorkspaceReviewTests.intent
    prepare = review_fixture.WorkspaceReviewTests.prepare
    assert_error = review_fixture.WorkspaceReviewTests.assert_error

    def fixture(self, *, dirty=False):
        (self.repo / "src/file.txt").write_text("".join(f"line {index}\n" for index in range(14)))
        self.git("add", ".")
        self.git("commit", "-qm", "multiline base")
        if dirty:
            self.edit(self.repo, 7, "Host uncommitted input")
        manifest = self.prepare("delivery")
        checkout = Path(manifest["path"])
        return manifest, checkout

    @staticmethod
    def edit(checkout, index, value, name="src/file.txt"):
        path = checkout / name
        lines = path.read_text().splitlines(True)
        lines[index] = value + "\n"
        path.write_text("".join(lines))

    def worker_commit(self, checkout, index=1):
        self.edit(checkout, index, "Worker change " + str(index))
        self.git("add", ".", cwd=checkout)
        self.git("commit", "-qm", "Worker change", cwd=checkout)
        return self.git("rev-parse", "HEAD", cwd=checkout).decode().strip()

    def target(self, manifest):
        target = self.root / "target"
        self.git("clone", "-q", str(self.repo), str(target))
        self.git("config", "user.name", "Host", cwd=target)
        self.git("config", "user.email", "host@example.invalid", cwd=target)
        self.git("checkout", "-q", "--detach", manifest["baseCommit"], cwd=target)
        return target

    def verify(self, manifest, sealed, target, **extra):
        return workspace.integration_verify(sealed, original_input=manifest, final_input=manifest,
                                            path=str(target), ref="HEAD", **extra)

    def test_multiple_worker_commits_are_reused_and_recovered_without_reobserving_head(self):
        manifest, checkout = self.fixture()
        self.worker_commit(checkout)
        head = self.worker_commit(checkout, 3)
        writer = workspace._write_once

        def crash(path, raw):
            if path.name == "output.json":
                raise OSError("crash after choosing the output")
            writer(path, raw)

        with patch.object(workspace, "_write_once", side_effect=crash):
            self.assert_error("WORKSPACE_IO_ERROR", workspace.seal, self.state, manifest, "task", "attempt")
        self.worker_commit(checkout, 5)
        with patch.object(workspace, "_verify", side_effect=AssertionError("recovery must use the chosen record")):
            sealed = workspace.seal(self.state, manifest, "task", "attempt")
        self.assertEqual(sealed["version"], 2)
        self.assertEqual(sealed["commitKind"], "worker")
        self.assertEqual(sealed["commit"], head)
        self.assertEqual(workspace.verify_sealed_output(manifest, manifest, sealed)["changedPaths"], ["src/file.txt"])

    def test_partial_commit_and_host_output_have_only_evidence_commits(self):
        manifest, checkout = self.fixture()
        head = self.worker_commit(checkout)
        self.edit(checkout, 3, "Uncommitted Worker content")
        sealed = workspace.seal(self.state, manifest, "task", "attempt")
        self.assertEqual(sealed["commitKind"], "evidence")
        self.assertNotEqual(sealed["commit"], head)
        self.assertEqual(sealed["snapshot"]["headCommit"], head)
        self.assertEqual(workspace.seal(self.state, manifest, "task", "attempt"), sealed)
        self.git("add", ".", cwd=checkout)
        self.git("commit", "-qm", "finish all changes", cwd=checkout)
        host = workspace.host_seal(self.state, manifest, "task", "attempt")
        self.assertEqual(host["commitKind"], "evidence")

    def test_unrelated_head_with_the_same_tree_is_never_reused(self):
        manifest, checkout = self.fixture()
        self.worker_commit(checkout)
        tree = self.git("rev-parse", "HEAD^{tree}", cwd=checkout).decode().strip()
        unrelated = self.git("commit-tree", tree, data=b"unrelated root\n").decode().strip()
        self.git("checkout", "-q", "--detach", unrelated, cwd=checkout)
        sealed = workspace.seal(self.state, manifest, "task", "attempt")
        self.assertEqual(sealed["commitKind"], "evidence")
        self.assertNotEqual(sealed["commit"], unrelated)

    def test_historical_seal_bytes_and_parent_rule_stay_fixed(self):
        manifest, checkout = self.fixture()
        self.worker_commit(checkout)
        self.worker_commit(checkout, 3)
        self.edit(checkout, 5, "uncommitted")
        sealed = workspace.seal(self.state, manifest, "task", "attempt")
        legacy = json.loads(json.dumps(sealed))
        for document in (legacy, legacy["snapshot"]):
            document["version"] = 1
            del document["commitKind"]
        legacy["snapshotSha256"] = workspace._sha(workspace._json(legacy["snapshot"]))
        directory = Path(sealed["diffPath"]).parent
        for filename in ("output.json", "pending.json"):
            (directory / filename).write_bytes(workspace._json(legacy))
        before = (directory / "output.json").read_bytes()
        self.assertEqual(workspace.seal(self.state, manifest, "task", "attempt"), legacy)
        self.assertEqual((directory / "output.json").read_bytes(), before)
        workspace.verify_sealed_output(manifest, manifest, legacy)
        legacy["commit"] = legacy["snapshot"]["commit"] = legacy["snapshot"]["headCommit"]
        legacy["tree"] = legacy["snapshot"]["tree"] = self.git("rev-parse", "HEAD^{tree}", cwd=checkout).decode().strip()
        raw = workspace._diff(self.repo, manifest["inputCommit"], legacy["commit"])
        legacy["diffSha256"] = legacy["snapshot"]["diffSha256"] = workspace._sha(raw)
        legacy["snapshotSha256"] = workspace._sha(workspace._json(legacy["snapshot"]))
        (directory / "output.patch").write_bytes(raw)
        self.assert_error("WORKSPACE_MANIFEST_CHANGED", workspace.verify_sealed_output, manifest, manifest, legacy)
        self.assert_error("WORKSPACE_MANIFEST_CHANGED", workspace._finish_output, self.repo, legacy,
                          directory, manifest=manifest)

    def test_clean_merge_cherry_pick_patch_delete_and_rename(self):
        # Each case uses the real Git integration operation as the oracle.
        for mode in ("merge", "cherry-pick", "patch", "delete", "rename"):
            with self.subTest(mode=mode):
                # Each invocation gets an independent fixture and object stores.
                case = GitDeliveryTests(methodName="runTest")
                case.setUp()
                self.addCleanup(case.doCleanups)
                manifest, checkout = case.fixture()
                case.worker_commit(checkout)
                if mode == "delete":
                    (checkout / "src/deleted.txt").write_text("delete\n")
                    # Delete an input path, in addition to the multiline edit.
                    case.git("add", ".", cwd=checkout)
                    case.git("commit", "-qm", "extra Worker commit", cwd=checkout)
                    (checkout / "src/file.txt").unlink()
                elif mode == "rename":
                    case.git("mv", "src/file.txt", "src/renamed.txt", cwd=checkout)
                if mode in ("delete", "rename"):
                    case.git("add", "-A", cwd=checkout)
                    case.git("commit", "-qm", "finish Worker", cwd=checkout)
                sealed = workspace.seal(case.state, manifest, "task", "attempt")
                target = case.target(manifest)
                if mode == "delete":
                    (target / "docs/readme.md").write_text("Host addition\n")
                else:
                    case.edit(target, 11, "Host other line")
                case.git("add", ".", cwd=target)
                case.git("commit", "-qm", "Host before", cwd=target)
                before = case.git("rev-parse", "HEAD", cwd=target).decode().strip()
                case.git("fetch", "-q", str(case.repo), sealed["ref"], cwd=target)
                if mode == "cherry-pick":
                    case.git("cherry-pick", manifest["inputCommit"] + ".." + sealed["commit"], cwd=target)
                elif mode == "patch":
                    case.git("apply", "--3way", "--index", data=Path(sealed["diffPath"]).read_bytes(), cwd=target)
                    case.git("commit", "-qm", "Host applies patch", cwd=target)
                else:
                    case.git("merge", "--no-ff", "-m", "Host merges Worker", sealed["commit"], cwd=target)
                before_index = (target / ".git/index").read_bytes()
                before_head = case.git("rev-parse", "HEAD", cwd=target)
                objects = case.git("cat-file", "--batch-all-objects", "--batch-check=%(objectname)", cwd=target)
                verified = case.verify(manifest, sealed, target,
                                       **({"before_commit": before} if mode in ("cherry-pick", "patch") else {}))
                self.assertTrue(verified["verified"], verified)
                self.assertEqual(verified["beforeCommit"], before)
                self.assertEqual(verified["strategy"], "three-way")
                self.assertEqual(verified["adjustments"], [])
                self.assertEqual(verified["differingPaths"], [])
                self.assertEqual(verified["hostPaths"], [])
                self.assertEqual((target / ".git/index").read_bytes(), before_index)
                self.assertEqual(case.git("rev-parse", "HEAD", cwd=target), before_head)
                self.assertEqual(case.git("cat-file", "--batch-all-objects", "--batch-check=%(objectname)", cwd=target), objects)

    def test_fast_forward_needs_before_and_ancestry_does_not_hide_revert(self):
        manifest, checkout = self.fixture()
        self.worker_commit(checkout)
        sealed = workspace.seal(self.state, manifest, "task", "attempt")
        target = self.target(manifest)
        self.git("fetch", "-q", str(self.repo), sealed["ref"], cwd=target)
        self.git("merge", "--ff-only", sealed["commit"], cwd=target)
        error = self.assert_error("INTEGRATION_UNVERIFIED", self.verify, manifest, sealed, target)
        self.assertIn("beforeCommit", str(error))
        self.assertTrue(self.verify(manifest, sealed, target, before_commit=manifest["inputCommit"])["verified"])
        self.git("revert", "--no-edit", sealed["commit"], cwd=target)
        reverted = self.git("rev-parse", "HEAD", cwd=target).decode().strip()
        verification = self.verify(manifest, sealed, target, before_commit=reverted)
        self.assertFalse(verification["verified"])
        adjusted = self.verify(manifest, sealed, target, before_commit=reverted,
                               _confirm_adjustments=True, note="Host deliberately reverted the Worker change")
        self.assertTrue(adjusted["verified"])
        self.assertEqual(adjusted["adjustments"], ["src/file.txt"])

    def test_conflicts_need_adjustment_even_if_host_keeps_the_worker_blob(self):
        manifest, checkout = self.fixture()
        self.worker_commit(checkout)
        sealed = workspace.seal(self.state, manifest, "task", "attempt")
        target = self.target(manifest)
        self.edit(target, 1, "Host conflicting change")
        self.git("add", ".", cwd=target)
        self.git("commit", "-qm", "Host before", cwd=target)
        before = self.git("rev-parse", "HEAD", cwd=target).decode().strip()
        (target / "src/file.txt").write_bytes((checkout / "src/file.txt").read_bytes())
        self.git("add", ".", cwd=target)
        self.git("commit", "-qm", "Host resolves with Worker contents", cwd=target)
        verification = self.verify(manifest, sealed, target, before_commit=before)
        self.assertFalse(verification["verified"])
        self.assertEqual(verification["conflictingPaths"], ["src/file.txt"])
        self.assert_error("INVALID_WORKSPACE", self.verify, manifest, sealed, target, before_commit=before,
                          _confirm_adjustments=True, note="")
        adjusted = self.verify(manifest, sealed, target, before_commit=before,
                               _confirm_adjustments=True, note="Chose Worker contents in conflict")
        self.assertTrue(adjusted["verified"])
        self.assertEqual(adjusted["strategy"], "three-way-adjusted")


class GitDeliveryWorkflowTests(LifecycleTestCase):
    def deliver(self, board, *, committed=True, disposition="completed", request_id="delivery", output="Worker output\n"):
        submitted = self.submit_run(board, {"cwd": str(self.repo), "title": request_id, "task": "Git delivery",
                                          "checkout": "worktree", "access": "write", "writeScope": ["."]})
        claimed = self.claim(board, submitted["runId"], request_id=request_id + "-claim")
        manifest = turn_manifest(self.directory, claimed["claim"]["turn"]["input"])
        checkout = Path(manifest["path"])
        (checkout / "tracked.txt").write_text(output)
        if committed:
            git(checkout, "add", ".")
            git(checkout, "commit", "-qm", "Worker output")
        seal = workspace.seal(self.directory, manifest, submitted["runId"], claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal, disposition=disposition)
        view = self.view(board, submitted["runId"])
        artifact = next(row for row in view["artifacts"] if row["kind"] == "output")
        return view, artifact, manifest, checkout, seal

    def test_source_requires_every_seal_to_be_a_worker_commit(self):
        for committed in (True, False):
            with self.subTest(committed=committed):
                board = self.board()
                view, artifact, initial, checkout, seal = self.deliver(
                    board, committed=committed, disposition="assistance", request_id="committed-" + str(committed))
                self.assertEqual(artifact["integrationSource"]["kind"], "commits" if committed else "patch")
                self.assertNotIn("outputCommit", artifact)
                self.assertNotIn("outputCommit", artifact["cumulativePatch"])
                self.continue_run(board, {"runId": view["runId"], "input": "finish"})
                claimed = self.claim(board, view["runId"], request_id="claim-final-" + str(committed))
                final_input = turn_manifest(self.directory, claimed["claim"]["turn"]["input"])
                (checkout / "tracked.txt").write_text("Worker final output\n")
                git(checkout, "add", ".")
                git(checkout, "commit", "-qm", "Worker final output")
                final = workspace.seal(self.directory, final_input, view["runId"], claimed["claim"]["attempt"]["attemptId"])
                workspace.cumulative_patch(final_input, final, initial["inputCommit"])
                self.finish_turn(board, claimed, final)
                delivered = self.view(board, view["runId"])
                output = next(row for row in delivered["artifacts"] if row["kind"] == "output")
                source = output["integrationSource"]
                self.assertEqual(source["kind"], "commits" if committed else "patch")
                self.assertEqual(source["baseCommit"], initial["inputCommit"])
                if committed:
                    self.assertEqual(source["headCommit"], final["commit"])
                else:
                    self.assertNotIn("headCommit", source)
                    self.assertNotIn(final["commit"], json.dumps(output))

    def test_host_dirty_input_delivers_patch_even_when_worker_commits_everything(self):
        (self.repo / "src/feature.py").write_text("Host uncommitted input\n")
        board = self.board()
        view, artifact, manifest, checkout, seal = self.deliver(board)
        self.assertNotEqual(manifest["inputCommit"], manifest["baseCommit"])
        self.assertEqual(seal["commitKind"], "worker")
        self.assertEqual(artifact["integrationSource"]["kind"], "patch")
        self.assertEqual(artifact["integrationSource"]["sha256"], artifact["cumulativePatch"]["sha256"])
        self.assertNotIn("headCommit", artifact["integrationSource"])
        self.assertNotIn(seal["commit"], json.dumps(artifact))

    def test_service_checks_a_claimed_worker_commit_before_publishing_it(self):
        board = self.board()
        submitted = self.submit_run(board, {"cwd": str(self.repo), "title": "forged commit", "task": "Git delivery",
                                          "checkout": "worktree", "access": "write", "writeScope": ["."]})
        claimed = self.claim(board, submitted["runId"])
        manifest = turn_manifest(self.directory, claimed["claim"]["turn"]["input"])
        checkout = Path(manifest["path"])
        (checkout / "tracked.txt").write_text("Worker output\n")
        git(checkout, "add", ".")
        git(checkout, "commit", "-qm", "Worker output")
        attempt_id = claimed["claim"]["attempt"]["attemptId"]
        sealed = workspace.seal(self.directory, manifest, submitted["runId"], attempt_id)
        unrelated = workspace._line(self.repo, "commit-tree", sealed["tree"], data=b"unrelated root\n")
        sealed["commit"] = sealed["snapshot"]["commit"] = sealed["snapshot"]["headCommit"] = unrelated
        sealed["snapshotSha256"] = workspace._sha(workspace._json(sealed["snapshot"]))
        Path(sealed["diffPath"]).with_name("output.json").write_bytes(workspace._json(sealed))
        with self.assertRaises(BoardError) as refused:
            board.store.workflow.precheck_result({"attemptId": attempt_id, "status": "failed",
                "result": {"workspaceSeal": record_reference(self.directory, sealed)}})
        self.assertEqual(refused.exception.code, "WORKSPACE_MANIFEST_CHANGED")

    def test_export_preserves_new_record_and_patch_bytes(self):
        board = self.board()
        view, artifact, manifest, checkout, sealed = self.deliver(board)
        self.accept(board, view, artifact, not_required=True)
        board.store.complete_execution_cleanup(sealed["attemptId"])
        exporter = runpy.run_path(str(Path(__file__).resolve().parents[4] / "tools/export_board_sample.py"))["export_sample"]
        with tempfile.TemporaryDirectory(prefix="git-delivery-sample-") as directory:
            sample = exporter(board.directory, Path(directory) / "sample")
            with sqlite3.connect(sample / "board.sqlite3") as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute("SELECT * FROM workflow_artifacts WHERE artifact_id=?", (artifact["artifactId"],)).fetchone()
                copied = artifact_manifest(sample, row)
            self.assertEqual(copied, sealed)
            self.assertEqual(copied["version"], 2)
            self.assertEqual((sample / row["record_path"]).with_suffix(".patch").read_bytes(), Path(sealed["diffPath"]).read_bytes())
            self.assertEqual((sample / row["record_path"]).read_bytes(), (board.directory / row["record_path"]).read_bytes())

    def test_acceptance_and_first_pass_follow_clean_merge_and_actual_host_changes(self):
        for mode in ("clean", "adjusted", "host-path", "host-path-inferred"):
            with self.subTest(mode=mode):
                base = [f"line {index}\n" for index in range(14)]
                (self.repo / "tracked.txt").write_text("".join(base))
                self.git("add", ".")
                self.git("commit", "--allow-empty", "-qm", "multiline input " + mode)
                output = list(base)
                output[1] = "Worker line\n"
                board = self.board()
                view, artifact, manifest, checkout, seal = self.deliver(
                    board, request_id=mode, output="".join(output))
                target = self.directory / ("target-" + mode)
                git(self.directory, "clone", "-q", str(self.repo), str(target))
                host = list(base)
                host[11] = "Host original line\n"
                (target / "tracked.txt").write_text("".join(host))
                git(target, "add", ".")
                git(target, "commit", "-qm", "Host before")
                before = git(target, "rev-parse", "HEAD").strip()
                git(target, "fetch", "-q", str(self.repo), seal["ref"])
                extra = {}
                if mode == "host-path-inferred":
                    git(target, "merge", "--no-ff", "--no-commit", seal["commit"])
                    (target / "host.txt").write_text("Host supplementary work\n")
                    git(target, "add", ".")
                    git(target, "commit", "-qm", "Integrate Worker with Host addition")
                    extra["hostPaths"] = ["host.txt"]
                else:
                    git(target, "merge", "--no-ff", "-m", "Integrate Worker", seal["commit"])
                if mode in ("adjusted", "host-path"):
                    extra["beforeCommit"] = before
                    if mode == "adjusted":
                        (target / "tracked.txt").write_text("Host rewrote the artifact\n")
                        extra["adjusted"] = True
                    else:
                        (target / "host.txt").write_text("Host supplementary work\n")
                        extra["hostPaths"] = ["host.txt"]
                    git(target, "add", ".")
                    git(target, "commit", "-qm", "Host review changes")
                packet = {"runId": view["runId"], "artifactId": artifact["artifactId"],
                          "target": {"path": str(target), "ref": "HEAD"}, "note": "Reviewed " + mode, **extra}
                if mode == "adjusted":
                    with self.assertRaises(BoardError) as refused:
                        board.call("workflow_accept", {key: value for key, value in packet.items() if key != "adjusted"})
                    self.assertEqual(refused.exception.code, "INTEGRATION_UNVERIFIED")
                    self.assertIn("adjusted:true", refused.exception.message)
                    self.assertNotIn("sourceCommit", refused.exception.details["verification"])
                accepted = board.call("workflow_accept", packet)
                self.assertEqual(accepted["state"], "accepted")
                integration = self.view(board, view["runId"])["integrations"][0]
                self.assertEqual(integration["strategy"], "three-way-adjusted" if mode == "adjusted" else "three-way")
                self.assertEqual(integration["beforeCommit"], before)
                self.assertEqual(integration["verification"]["hostPaths"], ["host.txt"] if mode.startswith("host-path") else [])
                with board.store.db.read() as connection:
                    sample = next(iter(samples_for_run(connection, view["runId"]).values()))
                self.assertEqual(sample["first_pass"], mode == "clean")
                replay = board.call("workflow_accept", packet)
                self.assertTrue(replay["alreadyResponded"])
