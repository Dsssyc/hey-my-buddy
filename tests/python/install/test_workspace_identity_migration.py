"""Schema-15 identity migration, exact preservation and atomic failure witnesses."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from unittest.mock import patch

from blackboard.tasks import test_workspace_identity as identity_fixtures
from hey_my_buddy.blackboard.store import workspace_identity_migration as migration
from hey_my_buddy.blackboard.store.workspace_identity_migration import migrate_workspace_identity
from hey_my_buddy.blackboard.tasks import workspace, workspace_identity
from hey_my_buddy.errors import BoardError


class WorkspaceIdentityMigrationTests(identity_fixtures.WorkspaceIdentityFixture):
    def retained(self, summary, manifest, reason=None):
        aliases = self.aliases(self._stack[-1])
        for old in {manifest["checkoutId"], manifest["repositoryId"]}:
            self.assertNotIn(old, aliases, summary)
            self.assertIn(old, summary["kept"], summary)
            if reason is not None:
                self.assertEqual(summary["kept"][old], reason, summary)

    def rewrite_manifest(self, board, run, manifest, mutate, *, valid_checksum=True):
        modified = copy.deepcopy(manifest)
        mutate(modified)
        if valid_checksum:
            payload = {key: value for key, value in modified.items() if key != "manifestSha256"}
            modified["manifestSha256"] = hashlib.sha256(workspace._json(payload)).hexdigest()
        value = json.dumps(modified, sort_keys=True, separators=(",", ":"))
        with board.store.db.write() as connection:
            connection.execute("UPDATE workflow_runs SET workspace_manifest_json=?,workspace_manifest_sha256=? WHERE run_id=?",
                               (value, modified["manifestSha256"], run["runId"]))
            connection.execute("UPDATE workflow_artifacts SET manifest_json=?,manifest_sha256=? WHERE run_id=?",
                               (value, modified["manifestSha256"], run["runId"]))
        (self.directory / "workspaces" / manifest["workspaceId"] / "manifest.json").write_text(value)
        return modified

    def test_normalizes_indexes_and_preserves_all_fixed_history_bytes(self):
        board, run, manifest = self.legacy_run()
        self.register(board)
        with self.legacy():
            claimed = self.claim(board, run_id=run["runId"])
            checkout = Path(manifest["checkoutRoot"])
            (checkout / "tracked.txt").write_text("retained old output\n")
            self.finish_turn(board, claimed, disposition="assistance")
        old_history = self.historical(board)
        old_files = self.fixed_files()
        before = self.snapshot(board)
        summary, expected = migrate_workspace_identity(self.directory, before)
        actual = workspace.inspect(manifest["path"])
        with board.store.db.read() as connection:
            objective = dict(connection.execute("SELECT * FROM objectives WHERE objective_id=?", (run["objectiveId"],)).fetchone())
            reservation = dict(connection.execute("SELECT * FROM workspace_reservations WHERE holder_task_id=?", (run["runId"],)).fetchone())
            entries = {key: json.loads(value) for key, value in connection.execute(
                "SELECT key,value FROM meta WHERE key LIKE 'workspace-identity:%'"
            )}
        self.assertEqual(objective["project_id"], actual["repositoryId"], summary)
        self.assertEqual(reservation["checkout_id"], actual["checkoutId"], summary)
        self.assertEqual(reservation["repository_id"], actual["repositoryId"], summary)
        self.assertEqual(self.historical(board), old_history)
        self.assertEqual(self.fixed_files(), old_files)
        self.assertEqual(self.snapshot(board), expected)
        self.assertEqual(expected["eventHead"], before["eventHead"])
        self.assertEqual(expected["schema"], 15)
        for table, fingerprint in before["fingerprints"].items():
            if table not in {"meta", "objectives", "workspace_reservations"}:
                self.assertEqual(expected["fingerprints"][table], fingerprint, table)
        for role, directory_field in (("checkoutId", "gitDir"), ("repositoryId", "repositoryPath")):
            entry = entries[workspace_identity.META_KEY_PREFIX + manifest[role]]
            self.assertEqual(entry["source"], "fixed-input-and-inode")
            self.assertEqual(entry["newId"], actual[role])
            witnesses = [anchor for anchor in entry["anchors"]
                         if anchor["role"] == role and anchor["workspaceId"] == manifest["workspaceId"]]
            self.assertTrue(witnesses, entry)
            directory = Path(actual[directory_field])
            self.assertTrue(any(anchor["directory"] == str(directory)
                                and anchor["inode"] == directory.stat().st_ino
                                and anchor["legacyDevice"] == directory.stat().st_dev + 2
                                and anchor["manifestSha256"] == manifest["manifestSha256"]
                                for anchor in witnesses), entry)

    def test_multiple_old_devices_map_to_one_current_inode_without_collision(self):
        board, first, old = self.legacy_run(kind="existing", access="read", request_id="before-first-restart")
        _board, second, later = self.legacy_run(board, kind="existing", access="read",
                                              device_delta=4, request_id="after-first-restart")
        self.assertNotEqual(old["repositoryId"], later["repositoryId"])
        self.migrate(board)
        # New admission obeys current read-sharing rules. Its independent
        # worktree has the same repository and a distinct private Git directory.
        current = self.submit(board, request_id="stable-current", cwd=str(self.repo),
                              executionWorkspace={"kind": "worktree", "access": "read"},
                              objective={"title": "current macro"})
        current_manifest = self.manifest(board, current["runId"])
        summary, _expected = self.migrate(board)
        actual = workspace.inspect(str(self.repo))
        with board.store.db.read() as connection:
            projects = [row[0] for row in connection.execute(
                "SELECT project_id FROM objectives WHERE objective_id IN (?,?,?)",
                (first["objectiveId"], second["objectiveId"], current["objectiveId"])
            )]
            held = [tuple(row) for row in connection.execute(
                "SELECT holder_task_id,checkout_id,repository_id FROM workspace_reservations WHERE state='held'"
            )]
        self.assertEqual(projects, [actual["repositoryId"]] * 3, summary)
        self.assertEqual({holder: (checkout, repository) for holder, checkout, repository in held}, {
            first["runId"]: (actual["checkoutId"], actual["repositoryId"]),
            second["runId"]: (actual["checkoutId"], actual["repositoryId"]),
            current["runId"]: (current_manifest["checkoutId"], actual["repositoryId"]),
        })
        self.assertEqual(self.aliases(board)[old["repositoryId"]], actual["repositoryId"])
        self.assertEqual(self.aliases(board)[later["repositoryId"]], actual["repositoryId"])
        self.assertNotIn(actual["repositoryId"], self.aliases(board))

    def test_second_migration_is_byte_idempotent_including_its_report(self):
        board, _run, _manifest = self.legacy_run()
        self.migrate(board)
        before = self.all_rows(board)
        before_files = self.fixed_files()
        self.migrate(board)
        self.assertEqual(self.all_rows(board), before)
        self.assertEqual(self.fixed_files(), before_files)

    def test_missing_checkout_keeps_old_indexes_and_does_not_reanchor_after_return(self):
        board, run, manifest = self.legacy_run()
        root = Path(manifest["checkoutRoot"])
        retained = root.parent / "retained-missing-checkout"
        root.rename(retained)
        before = self.historical(board)
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest, "path-missing")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT project_id FROM objectives WHERE objective_id=?", (run["objectiveId"],)).fetchone()[0],
                             manifest["repositoryId"])
            self.assertEqual(connection.execute("SELECT checkout_id FROM workspace_reservations WHERE holder_task_id=?", (run["runId"],)).fetchone()[0],
                             manifest["checkoutId"])
            self.assertEqual(workspace_identity.identity_reason(connection, manifest["checkoutId"]), "path-missing")
        task = board.call("task_get", {"runId": run["runId"]})["task"]
        self.assertEqual(task["delegation"]["project"]["id"], manifest["repositoryId"])
        self.assertEqual(task["delegation"]["project"]["identityReason"], "path-missing")
        timeline = board.call("objective_timeline", {"objectiveId": run["objectiveId"]})
        self.assertEqual(timeline["objective"]["project"]["identityReason"], "path-missing")
        retained.rename(root)
        second, _expected = self.migrate(board)
        self.retained(second, manifest, "path-missing")
        self.assertEqual(self.historical(board), before)
        with patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory)}):
            with self.assertRaises(BoardError):
                workspace.verify(manifest, require_unchanged=False)

    def test_copying_all_fixed_refs_to_a_new_inode_does_not_prove_the_old_directory(self):
        board, run, manifest = self.legacy_run(kind="existing")
        git_dir = self.repo / ".git"
        retained = self.directory / "saved-original-git"
        git_dir.rename(retained)
        shutil.copytree(retained, git_dir)
        self.assertNotEqual(git_dir.stat().st_ino, retained.stat().st_ino)
        self.assertEqual(self.git(self.repo, "rev-parse", manifest["snapshot"]["inputRef"]), manifest["inputCommit"])
        self.assertEqual(self.git(self.repo, "rev-parse", manifest["snapshot"]["stagedRef"]), manifest["snapshot"]["stagedCommit"])
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest, "legacy-inode-unproven")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT project_id FROM objectives WHERE objective_id=?", (run["objectiveId"],)).fetchone()[0],
                             manifest["repositoryId"])
        with patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory)}):
            with self.assertRaises(BoardError):
                workspace.verify(manifest, require_unchanged=False)

    def test_invalid_manifest_checksum_is_retained_without_trusting_paths(self):
        board, run, manifest = self.legacy_run()
        self.rewrite_manifest(board, run, manifest, lambda value: value.update(inputTree="0" * 40), valid_checksum=False)
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest)

    def test_manifest_with_extra_fields_does_not_supply_trusted_identity_evidence(self):
        board, run, manifest = self.legacy_run()
        self.rewrite_manifest(board, run, manifest, lambda value: value.update(untrustedProof=True))
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest)

    def test_a_legacy_device_outside_the_provable_range_keeps_the_original_indexes(self):
        board, run, manifest = self.legacy_run(device_delta=1 << 32)
        historical = self.historical(board)
        fixed = self.fixed_files()
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest, "legacy-inode-unproven")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT project_id FROM objectives WHERE objective_id=?", (run["objectiveId"],)).fetchone()[0],
                             manifest["repositoryId"])
        self.assertEqual(self.historical(board), historical)
        self.assertEqual(self.fixed_files(), fixed)

    def test_unproven_legacy_holder_blocks_a_stable_writer_in_sibling_cwd(self):
        left, right = self.repo / "left", self.repo / "right"
        left.mkdir()
        right.mkdir()
        board = self.board()
        with self.legacy(device_delta=1 << 32):
            run = self.submit(board, request_id="unproven-holder", cwd=str(left))
        manifest = self.manifest(board, run["runId"])
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest, "legacy-inode-unproven")
        self.assertNotEqual(workspace.inspect(str(right))["checkoutId"], manifest["checkoutId"])
        history = self.historical(board)
        for access in ("write", "read"):
            with self.assertRaises(BoardError) as caught:
                self.submit(board, request_id="unproven-competitor-" + access, cwd=str(right),
                            executionWorkspace={"kind": "existing", "access": access})
            self.assertEqual(caught.exception.code, "PREPARATION_CONFLICT")
        with board.store.db.write() as connection:
            connection.execute("UPDATE workspace_reservations SET state='transferred' WHERE holder_task_id=?", (run["runId"],))
        with self.assertRaises(BoardError) as caught:
            self.submit(board, request_id="unproven-transferred-competitor", cwd=str(right))
        self.assertEqual(caught.exception.code, "PREPARATION_CONFLICT")
        self.assertEqual(board.store.count_tasks(), 1)
        self.assertEqual(self.historical(board), history)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT checkout_id FROM workspace_reservations WHERE state IN ('held','transferred')").fetchone()[0], manifest["checkoutId"])

    def test_returned_missing_path_does_not_admit_a_second_stable_holder(self):
        board, _run, manifest = self.legacy_run()
        root = Path(manifest["checkoutRoot"])
        actual = workspace.inspect(str(root))
        root_inode = root.stat().st_ino
        git_inode = Path(actual["gitDir"]).stat().st_ino
        history = self.historical(board)
        fixed = self.fixed_files()
        saved = root.parent / "saved-absent-checkout"
        root.rename(saved)
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest, "path-missing")
        saved.rename(root)
        self.assertEqual(root.stat().st_ino, root_inode)
        self.assertEqual(Path(actual["gitDir"]).stat().st_ino, git_inode)
        self.assertEqual(workspace.inspect(str(root)), actual)
        before = self.all_rows(board)
        # B2 verifies the original managed allocation before capturing borrowed
        # input or entering reservation admission. B1 deliberately did not
        # register an alias while the path was missing; return alone is no proof.
        with patch.object(workspace, "_prepare_locked", wraps=workspace._prepare_locked) as prepare, patch.object(
                board.store.workflow, "_reserve", wraps=board.store.workflow._reserve) as reserve:
            with self.assertRaises(BoardError) as caught:
                self.submit(board, request_id="returned-competitor", cwd=str(root),
                            executionWorkspace={"kind": "existing", "access": "write"})
            self.assertEqual(caught.exception.code, "WORKSPACE_CHANGED")
            self.assertEqual(caught.exception.details, {"field": "checkoutId"})
            prepare.assert_not_called()
            reserve.assert_not_called()
        self.assertEqual(self.all_rows(board), before)
        self.assertEqual(self.historical(board), history)
        self.assertEqual(self.fixed_files(), fixed)
        self.assertEqual(root.stat().st_ino, root_inode)
        self.assertEqual(workspace.inspect(str(root)), actual)
        self.assertEqual((root / "tracked.txt").read_text(), "base\n")
        self.assertEqual(board.store.count_tasks(), 1)

    def test_returned_missing_existing_checkout_reaches_unproven_reservation_guard(self):
        # An unmanaged checkout has no borrowed-allocation guard. Real input
        # preparation must reach B1's atomic reservation gate for this witness.
        board, run, manifest = self.legacy_run(kind="existing")
        root = Path(manifest["checkoutRoot"])
        saved = root.parent / "saved-absent-existing-checkout"
        history = self.historical(board)
        fixed = self.fixed_files()
        root.rename(saved)
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest, "path-missing")
        saved.rename(root)
        actual = workspace.inspect(str(root))
        root_inode = root.stat().st_ino
        before = self.all_rows(board)
        with patch.object(workspace, "_prepare_locked", wraps=workspace._prepare_locked) as prepare, patch.object(
                board.store.workflow, "_reserve", wraps=board.store.workflow._reserve) as reserve:
            with self.assertRaises(BoardError) as caught:
                self.submit(board, request_id="returned-existing-competitor", cwd=str(root),
                            executionWorkspace={"kind": "existing", "access": "write"})
            self.assertEqual(caught.exception.code, "PREPARATION_CONFLICT")
            self.assertEqual(caught.exception.details, {
                "checkoutId": actual["checkoutId"], "holderTaskId": run["runId"],
                "recordedCheckoutId": manifest["checkoutId"], "identityReason": "path-missing",
            })
            prepare.assert_called_once()
            reserve.assert_called_once()
        self.assertEqual(self.all_rows(board), before)
        self.assertEqual(self.historical(board), history)
        # A refused existing capture can retain its own prepared files for
        # replay, but cannot rewrite any original fixed history or gain a holder.
        after_files = self.fixed_files()
        self.assertEqual({path: after_files[path] for path in fixed}, fixed)
        self.assertEqual(root.stat().st_ino, root_inode)
        self.assertEqual(workspace.inspect(str(root)), actual)
        self.assertEqual((root / "tracked.txt").read_text(), "base\n")
        self.assertEqual(board.store.count_tasks(), 1)

    def test_manifest_checkout_root_must_match_the_git_execution_checkout(self):
        board, run, manifest = self.legacy_run()
        self.rewrite_manifest(board, run, manifest, lambda value: value.update(checkoutRoot=str(self.repo)))
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest)

    def test_missing_fixed_input_ref_is_retained_with_no_alias(self):
        board, _run, manifest = self.legacy_run()
        ref_path = Path(manifest["snapshot"]["repositoryPath"]) / manifest["snapshot"]["inputRef"]
        ref_path.rename(self.directory / "saved-input-ref")
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest)

    def tag_object(self, manifest, field):
        commit = manifest["inputCommit"] if field == "inputRef" else manifest["snapshot"]["stagedCommit"]
        tags_before = self.git(self.repo, "for-each-ref", "refs/tags")
        payload = (f"object {commit}\ntype commit\ntag identity-proof-{field}\n"
                   "tagger Workspace Identity Test <identity@example.invalid> 1760000000 +0000\n"
                   "\nPrivate identity proof object\n").encode()
        tag = workspace._git(self.repo, "mktag", data=payload).decode().strip()
        ref = manifest["snapshot"][field]
        self.git(self.repo, "update-ref", ref, tag)
        self.assertEqual(self.git(self.repo, "cat-file", "-t", tag), "tag")
        self.assertEqual(self.git(self.repo, "rev-parse", ref), tag)
        self.assertNotEqual(tag, commit)
        self.assertEqual(self.git(self.repo, "rev-parse", ref + "^{commit}"), commit)
        self.assertEqual(self.git(self.repo, "for-each-ref", "refs/tags"), tags_before)

    def tagged_ref_is_retained(self, field):
        board, run, manifest = self.legacy_run(kind="existing")
        self.tag_object(manifest, field)
        history = self.historical(board)
        files = self.fixed_files()
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest, "fixed-refs-unverified")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT project_id FROM objectives WHERE objective_id=?",
                                                (run["objectiveId"],)).fetchone()[0], manifest["repositoryId"])
            reservation = connection.execute("SELECT checkout_id,repository_id FROM workspace_reservations WHERE holder_task_id=?",
                                             (run["runId"],)).fetchone()
            self.assertEqual(tuple(reservation), (manifest["checkoutId"], manifest["repositoryId"]))
        self.assertEqual(self.historical(board), history)
        self.assertEqual(self.fixed_files(), files)

    def test_annotated_tag_input_ref_with_the_same_commit_cannot_prove_an_alias(self):
        self.tagged_ref_is_retained("inputRef")

    def test_annotated_tag_staged_ref_with_the_same_commit_cannot_prove_an_alias(self):
        self.tagged_ref_is_retained("stagedRef")

    def save_manifest_artifact(self, connection, run, manifest, label, *, binding=None):
        connection.execute(
            "INSERT INTO workflow_artifacts(artifact_id,run_id,kind,manifest_json,manifest_sha256,created_at)"
            " VALUES(?,?,'input',?,?,?)", (label, run["runId"], migration._json(manifest),
                                          manifest["manifestSha256"] if binding is None else binding, "then"))

    def save_manifest_turn(self, connection, run, manifest, index, *, valid_binding=True):
        value = migration._json({"executionWorkspace": manifest, "fixtureTurn": index})
        connection.execute(
            "INSERT INTO workflow_turns(turn_id,run_id,turn_index,resume_mode,input_json,input_sha256,state,created_at,updated_at)"
            " VALUES(?,?,?,'initial',?,?,'prepared','then','then')",
            ("identity-duplicate-turn-" + str(index), run["runId"], index, value,
             hashlib.sha256(value.encode()).hexdigest() if valid_binding else "0" * 64))

    def proof_cost(self, board):
        counts = {"legacyHashes": 0}
        legacy = workspace_identity.legacy_device
        sha256 = hashlib.sha256

        def counted_legacy(*args):
            # Count only physical legacy-device digest reconstruction. Each raw
            # turn JSON still needs its own independently checked row binding.
            with patch.object(workspace_identity.hashlib, "sha256", wraps=sha256) as hashes:
                result = legacy(*args)
                counts["legacyHashes"] += hashes.call_count
            return result

        with patch.object(migration, "_proof", wraps=migration._proof) as proof, patch.object(
                workspace.subprocess, "run", wraps=workspace.subprocess.run) as processes, patch.object(
                workspace_identity, "legacy_device", side_effect=counted_legacy) as devices:
            with board.store.db.read() as connection:
                records = migration._manifests(connection)
                result = migration._plan(connection, self.directory)
            counts.update(proofs=proof.call_count, legacyDevices=devices.call_count,
                          gitProcesses=sum(call.args[0][0] == "git" for call in processes.call_args_list))
        return result, records, counts

    def test_repeated_database_manifests_reuse_one_physical_proof(self):
        board, run, manifest = self.legacy_run(kind="existing")
        first, records_before, cost_before = self.proof_cost(board)
        self.assertEqual(len(records_before), 2)
        self.assertTrue(all(bound for value, bound in records_before))
        with board.store.db.write() as connection:
            for index in range(5):
                self.save_manifest_artifact(connection, run, manifest, "identity-duplicate-artifact-" + str(index))
                self.save_manifest_turn(connection, run, manifest, index + 1)
        repeated, records_after, cost_after = self.proof_cost(board)
        self.assertEqual(len(records_after), 12)
        self.assertTrue(all(bound for value, bound in records_after))
        self.assertTrue(all(value == manifest for value, bound in records_after))
        print(json.dumps({"duplicateManifestCosts": {"beforeRecords": len(records_before),
              "afterRecords": len(records_after), "before": cost_before, "after": cost_after}}, sort_keys=True))
        self.assertEqual(first, repeated)
        self.assertEqual(cost_before["proofs"], 1, cost_before)
        self.assertGreater(cost_before["gitProcesses"], 0)
        self.assertGreater(cost_before["legacyHashes"], 0)
        self.assertEqual(cost_after, cost_before)
        history, files = self.historical(board), self.fixed_files()
        summary, _expected = self.migrate(board)
        actual = workspace.inspect(manifest["path"])
        self.assertEqual(self.aliases(board)[manifest["repositoryId"]], actual["repositoryId"], summary)
        self.assertEqual(self.historical(board), history)
        self.assertEqual(self.fixed_files(), files)

    def test_repeated_unproven_database_manifests_reuse_the_same_denial(self):
        board, run, manifest = self.legacy_run(kind="existing", device_delta=1 << 32)
        first, records_before, cost_before = self.proof_cost(board)
        with board.store.db.write() as connection:
            for index in range(5):
                self.save_manifest_artifact(connection, run, manifest, "identity-unproven-copy-" + str(index))
        repeated, records_after, cost_after = self.proof_cost(board)
        self.assertEqual(len(records_before), 2)
        self.assertEqual(len(records_after), 7)
        self.assertEqual(first, repeated)
        self.assertEqual(first[2][manifest["repositoryId"]], "legacy-inode-unproven")
        print(json.dumps({"unprovenDuplicateCosts": {"beforeRecords": len(records_before),
              "afterRecords": len(records_after), "before": cost_before, "after": cost_after}}, sort_keys=True))
        self.assertEqual(cost_before["proofs"], 1, cost_before)
        self.assertGreater(cost_before["legacyHashes"], 0)
        self.assertEqual(cost_after, cost_before)
        history = self.historical(board)
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest, "legacy-inode-unproven")
        self.assertEqual(self.historical(board), history)

    def test_bad_row_bindings_and_claimed_checksums_do_not_mask_valid_proof(self):
        board, run, manifest = self.legacy_run(kind="existing")
        bad_checksum = copy.deepcopy(manifest)
        bad_checksum.update(checkoutId="a" * 64, repositoryId="a" * 64)
        unbound = copy.deepcopy(manifest)
        unbound.update(checkoutId="b" * 64, repositoryId="b" * 64)
        unbound["manifestSha256"] = hashlib.sha256(workspace._json(
            {key: value for key, value in unbound.items() if key != "manifestSha256"})).hexdigest()
        with board.store.db.write() as connection:
            original_artifact = connection.execute("SELECT artifact_id FROM workflow_artifacts WHERE run_id=?",
                                                   (run["runId"],)).fetchone()[0]
            self.save_manifest_artifact(connection, run, manifest, "identity-valid-duplicate")
            self.save_manifest_artifact(connection, run, bad_checksum, "identity-false-checksum")
            self.save_manifest_artifact(connection, run, unbound, "identity-unbound-artifact", binding="0" * 64)
            self.save_manifest_turn(connection, run, manifest, 1)
            self.save_manifest_turn(connection, run, unbound, 2, valid_binding=False)
        for valid_first in (False, True):
            with self.subTest(valid_first=valid_first):
                with board.store.db.write() as connection:
                    connection.execute("UPDATE workflow_runs SET workspace_manifest_sha256=?",
                                       (manifest["manifestSha256"] if valid_first else "0" * 64,))
                    connection.execute("UPDATE workflow_artifacts SET manifest_sha256=? WHERE artifact_id=?",
                                       ("0" * 64 if valid_first else manifest["manifestSha256"], original_artifact))
                result, records, costs = self.proof_cost(board)
                self.assertEqual(len(records), 7)
                identical_bindings = [bound for value, bound in records if value == manifest]
                self.assertEqual(identical_bindings[0], valid_first)
                self.assertIn(False, identical_bindings)
                self.assertIn(True, identical_bindings)
                mapping, additions, kept, _registered = result
                current = workspace.inspect(manifest["path"])
                self.assertEqual(mapping[manifest["repositoryId"]], current["repositoryId"])
                self.assertIn(manifest["repositoryId"], additions)
                self.assertNotIn("a" * 64, additions)
                self.assertNotIn("b" * 64, additions)
                self.assertEqual(kept["a" * 64], "invalid-manifest")
                self.assertEqual(kept["b" * 64], "record-binding-invalid")
                self.assertEqual(costs["proofs"], 2)  # One real proof and one invalid checksum.
        history = self.historical(board)
        summary, _expected = self.migrate(board)
        self.assertEqual(self.aliases(board)[manifest["repositoryId"]], current["repositoryId"], summary)
        self.assertEqual(self.historical(board), history)

    def test_invalid_duplicate_row_bindings_never_reuse_a_previous_plan_proof(self):
        board, run, manifest = self.legacy_run(kind="existing")
        with board.store.db.write() as connection:
            self.save_manifest_artifact(connection, run, manifest, "identity-unbound-copy")
            self.save_manifest_turn(connection, run, manifest, 1)
        first, _records, costs = self.proof_cost(board)
        self.assertIn(manifest["repositoryId"], first[1])
        self.assertGreater(costs["gitProcesses"], 0)
        with board.store.db.write() as connection:
            connection.execute("UPDATE workflow_runs SET workspace_manifest_sha256=?", ("0" * 64,))
            connection.execute("UPDATE workflow_artifacts SET manifest_sha256=?", ("0" * 64,))
            connection.execute("UPDATE workflow_turns SET input_sha256=?", ("0" * 64,))
        second, records, costs = self.proof_cost(board)
        self.assertEqual(len(records), 4)
        self.assertTrue(all(not bound for value, bound in records))
        self.assertEqual(costs, {"proofs": 0, "gitProcesses": 0, "legacyDevices": 0, "legacyHashes": 0})
        self.assertEqual(second[1], {})
        self.assertEqual(second[2][manifest["repositoryId"]], "record-binding-invalid")
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest, "record-binding-invalid")

    def test_next_plan_rechecks_physical_refs_instead_of_reusing_a_saved_proof(self):
        board, _run, manifest = self.legacy_run(kind="existing")
        first, _records, before = self.proof_cost(board)
        self.assertIn(manifest["repositoryId"], first[1])
        self.assertGreater(before["gitProcesses"], 0)
        self.tag_object(manifest, "inputRef")
        second, _records, after = self.proof_cost(board)
        self.assertGreater(after["gitProcesses"], 0)
        self.assertNotIn(manifest["repositoryId"], second[0])
        self.assertNotIn(manifest["repositoryId"], second[1])
        self.assertEqual(second[2][manifest["repositoryId"]], "fixed-refs-unverified")

    def test_symbolic_input_ref_with_the_same_commit_cannot_prove_an_alias(self):
        board, run, manifest = self.legacy_run()
        reference = "refs/buddy/identity-proof-fixture/" + manifest["workspaceId"]
        self.git(self.repo, "update-ref", reference, manifest["inputCommit"])
        ref_path = Path(manifest["snapshot"]["repositoryPath"]) / manifest["snapshot"]["inputRef"]
        ref_path.write_text("ref: " + reference + "\n")
        self.assertEqual(self.git(self.repo, "rev-parse", manifest["snapshot"]["inputRef"]), manifest["inputCommit"])
        original = self.historical(board)
        summary, _expected = self.migrate(board)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT project_id FROM objectives WHERE objective_id=?", (run["objectiveId"],)).fetchone()[0], manifest["repositoryId"])
        self.retained(summary, manifest, "fixed-refs-unverified")
        self.assertEqual(self.historical(board), original)

    def test_drifted_alias_value_cannot_redirect_public_project_reads_or_filters(self):
        board, run, manifest = self.legacy_run(kind="existing")
        self.migrate(board)
        original = self.historical(board)
        old = manifest["repositoryId"]
        wrong = "f" * 64
        with board.store.db.write() as connection:
            key = workspace_identity.META_KEY_PREFIX + old
            entry = json.loads(connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0])
            entry["newId"] = wrong
            connection.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(entry), key))
        task = board.call("task_get", {"runId": run["runId"]})["task"]
        self.assertEqual(task["delegation"]["project"]["id"], old)
        listed = board.call("task_list", {"projectId": old})
        self.assertIn(run["runId"], {row["runId"] for row in listed["runs"]})
        redirected = board.call("task_list", {"projectId": wrong})
        self.assertNotIn(run["runId"], {row["runId"] for row in redirected["runs"]})
        with patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory)}):
            with self.assertRaises(BoardError):
                workspace.verify(manifest, require_unchanged=False)
        self.assertEqual(self.historical(board), original)

    def test_foreign_managed_worktree_lock_cannot_prove_an_alias(self):
        board, _run, manifest = self.legacy_run()
        actual = workspace.inspect(manifest["path"])
        (Path(actual["gitDir"]) / "locked").write_text("foreign fixture owner\n")
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest)

    def test_reservation_collision_keeps_both_old_writers_and_rejects_a_third(self):
        board, first, old = self.legacy_run(kind="existing", access="read", request_id="old-writer-one")
        _board, second, later = self.legacy_run(board, kind="existing", access="read",
                                              device_delta=4, request_id="old-writer-two")
        # Reproduce the legal old per-hash writer index after two restarts. The
        # real manifests still prove both original path/inode identities.
        with board.store.db.write() as connection:
            connection.execute("UPDATE workspace_reservations SET access='write' WHERE holder_task_id IN (?,?)",
                               (first["runId"], second["runId"]))
        held_before = self.all_rows(board)["workspace_reservations"]
        summary, _expected = self.migrate(board)
        actual = workspace.inspect(str(self.repo))
        aliases = self.aliases(board)
        for manifest in (old, later):
            self.assertEqual(summary["kept"][manifest["checkoutId"]], "reservation-conflict")
            self.assertEqual(aliases[manifest["checkoutId"]], actual["checkoutId"])
        held_after = self.all_rows(board)["workspace_reservations"]
        self.assertEqual(len(held_after), len(held_before))
        for was, now in zip(held_before, held_after):
            self.assertEqual(now[:3], was[:3])
            self.assertIn(now[3], {was[3], aliases.get(was[3], was[3])})
            self.assertEqual(now[4:], was[4:])
        with self.assertRaises(BoardError):
            self.submit(board, request_id="third-writer", cwd=str(self.repo),
                        executionWorkspace={"kind": "existing", "access": "write"})
        self.assertEqual(len([row for row in self.all_rows(board)["workspace_reservations"] if row[10] == "held"]), 2)

    def test_a_conflicting_registered_mapping_is_kept_without_overwrite(self):
        board, _run, manifest = self.legacy_run(kind="existing")
        other = self.repository("other")
        actual = workspace.inspect(manifest["path"])
        false_target = workspace.inspect(str(other))["repositoryId"]
        old = manifest["repositoryId"]
        value = json.dumps({"version": 1, "newId": false_target, "source": "fixed-input-and-inode", "anchors": [{
            "directory": actual["repositoryPath"], "inode": Path(actual["repositoryPath"]).stat().st_ino,
            "legacyDevice": Path(actual["repositoryPath"]).stat().st_dev + 2,
            "manifestSha256": manifest["manifestSha256"], "workspaceId": manifest["workspaceId"], "role": "repositoryId",
        }]}, sort_keys=True, separators=(",", ":"))
        with board.store.db.write() as connection:
            connection.execute("INSERT INTO meta(key,value) VALUES(?,?)", (workspace_identity.META_KEY_PREFIX + old, value))
        summary, _expected = self.migrate(board)
        self.assertEqual(summary["kept"][old], "mapping-conflict")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key=?", (workspace_identity.META_KEY_PREFIX + old,)).fetchone()[0], value)
            self.assertEqual(connection.execute("SELECT project_id FROM objectives").fetchone()[0], old)

    def test_unsupported_existing_alias_is_retained_and_cannot_authorize_the_checkout(self):
        board, _run, manifest = self.legacy_run(kind="existing")
        old = manifest["repositoryId"]
        value = json.dumps({"version": 2, "newId": workspace.inspect(str(self.repo))["repositoryId"]})
        key = workspace_identity.META_KEY_PREFIX + old
        with board.store.db.write() as connection:
            connection.execute("INSERT INTO meta(key,value) VALUES(?,?)", (key, value))
        summary, _expected = self.migrate(board)
        self.retained(summary, manifest, "mapping-conflict")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0], value)
            self.assertEqual(connection.execute("SELECT project_id FROM objectives").fetchone()[0], old)
            self.assertEqual(workspace_identity.identity_reason(connection, old), "mapping-conflict")
        with patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory)}):
            with self.assertRaises(BoardError):
                workspace.verify(manifest, require_unchanged=False)

    def test_before_snapshot_change_refuses_migration_without_new_writes(self):
        board, _run, _manifest = self.legacy_run()
        before = self.snapshot(board)
        with board.store.db.write() as connection:
            connection.execute("INSERT INTO meta(key,value) VALUES('fixture-unrelated','changed-after-backup')")
        changed = self.all_rows(board)
        with self.assertRaises(BoardError) as caught:
            migrate_workspace_identity(self.directory, before)
        self.assertEqual(caught.exception.code, "UPGRADE_MIGRATION_FAILED")
        self.assertEqual(self.all_rows(board), changed)

    def test_event_head_change_after_backup_is_not_hidden_by_snapshot_cutoff(self):
        board, _run, _manifest = self.legacy_run()
        before = self.snapshot(board)
        self.submit(board, request_id="after-backup", cwd=str(self.repo),
                    executionWorkspace={"kind": "worktree", "access": "read"})
        changed = self.all_rows(board)
        with self.assertRaises(BoardError):
            migrate_workspace_identity(self.directory, before)
        self.assertEqual(self.all_rows(board), changed)

    def test_injected_statement_failure_rolls_back_aliases_and_index_changes(self):
        board, _run, _manifest = self.legacy_run()
        with board.store.db.write() as connection:
            connection.execute("CREATE TRIGGER identity_fixture_fail BEFORE UPDATE OF project_id ON objectives BEGIN SELECT RAISE(ABORT,'private migration failure'); END")
        before = self.all_rows(board)
        with self.assertRaises((BoardError, sqlite3.DatabaseError)):
            self.migrate(board)
        self.assertEqual(self.all_rows(board), before)

    def collateral(self, statement):
        board, _run, _manifest = self.legacy_run()
        self.register(board)
        with board.store.db.write() as connection:
            connection.execute("CREATE TRIGGER identity_fixture_collateral AFTER UPDATE OF project_id ON objectives BEGIN " + statement + "; END")
        before = self.all_rows(board)
        with self.assertRaises(BoardError) as caught:
            self.migrate(board)
        self.assertEqual(caught.exception.code, "UPGRADE_MIGRATION_FAILED")
        self.assertEqual(self.all_rows(board), before)

    def test_trigger_cannot_change_unrelated_task_rows(self):
        self.collateral("UPDATE tasks SET queue_reason='fixture-rewritten'")

    def test_trigger_cannot_change_mutable_objective_columns_outside_project_id(self):
        self.collateral("UPDATE objectives SET title='fixture-rewritten'")

    def test_trigger_cannot_change_workers_omitted_from_backup_fingerprints(self):
        self.collateral("UPDATE workers SET state='lost'")

    def test_trigger_cannot_add_events_above_the_backup_event_head(self):
        self.collateral("INSERT INTO events(kind,payload_json,created_at) VALUES('fixture-collateral','{}','then')")

    def test_trigger_cannot_add_an_extra_alias_under_the_allowed_meta_prefix(self):
        self.collateral("INSERT INTO meta(key,value) VALUES('workspace-identity:" + "e" * 64 + "','{}')")

    def test_trigger_cannot_rewrite_a_preexisting_alias_or_unrelated_meta(self):
        board, _run, first_manifest = self.legacy_run(kind="existing", access="read")
        self.migrate(board)
        key = workspace_identity.META_KEY_PREFIX + first_manifest["repositoryId"]
        _board, _run, _manifest = self.legacy_run(board, request_id="second-device", device_delta=4,
                                               kind="existing", access="read")
        with board.store.db.write() as connection:
            connection.execute("CREATE TRIGGER identity_fixture_alias AFTER INSERT ON meta WHEN NEW.key LIKE 'workspace-identity:%' BEGIN UPDATE meta SET value='{}' WHERE key='" + key + "'; END")
        before = self.all_rows(board)
        with self.assertRaises(BoardError):
            self.migrate(board)
        self.assertEqual(self.all_rows(board), before)
