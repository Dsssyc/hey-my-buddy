"""The schema cutover removes obsolete seal fields from every durable copy."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import backup, upgrade
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
from hey_my_buddy.buddy.roles.turn_io import input_hash, validated_checkpoint
from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.install import upgrade as installer


class SchemaUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="schema-25-upgrade-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.state = load_fixture(self.root / "state")["state"]

    def _backup_and_apply(self):
        plan = upgrade.plan_schema_25_to_26(self.state)
        archive = Path(backup.create(self.state, physical_files={
            path: original for path, (original, _) in plan["physical"].items()})["path"])
        result = upgrade.upgrade_schema_25_to_26(self.state, plan=plan)
        return plan, archive, result

    def test_all_saved_copies_and_real_artifact_kinds(self):
        with sqlite3.connect(self.state / "board.sqlite3") as connection:
            kinds = {row[0] for row in connection.execute("SELECT kind FROM workflow_artifacts")}
            self.assertTrue({"shared-refs", "input", "output", "partial-output"}.issubset(kinds))
            row = connection.execute("SELECT run_id,turn_id,source_task_id,manifest_json,manifest_sha256,created_at "
                                     "FROM workflow_artifacts WHERE kind='output' LIMIT 1").fetchone()
            connection.execute("INSERT INTO workflow_artifacts(artifact_id,run_id,turn_id,attempt_id,source_task_id,kind,"
                               "manifest_json,manifest_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                               ("resolution-fixture", row[0], row[1], "resolution-fixture", row[2],
                                "resolved-output", row[3], row[4], row[5]))
            connection.execute("INSERT INTO workflow_artifacts(artifact_id,run_id,turn_id,attempt_id,source_task_id,kind,"
                               "manifest_json,manifest_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                               ("turn-input-fixture", row[0], row[1], "turn-input-fixture", row[2],
                                "turn-input", json.dumps({"manifestSha256": "input-identity"}),
                                "input-identity", row[5]))
            connection.execute("INSERT INTO workflow_artifacts(artifact_id,run_id,turn_id,attempt_id,source_task_id,kind,"
                               "manifest_json,manifest_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                               ("custom-seal-fixture", row[0], row[1], "custom-seal-fixture", row[2],
                                "future-seal-kind", row[3], row[4], row[5]))
        plan, _, result = self._backup_and_apply()
        self.assertEqual(result["schema"], SCHEMA_VERSION)
        self.assertGreaterEqual(result["convertedArtifacts"], 5)
        self.assertGreater(len(plan["physical"]), 0)
        with sqlite3.connect(self.state / "board.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                             str(SCHEMA_VERSION))
            for raw, digest in connection.execute("SELECT manifest_json,manifest_sha256 FROM workflow_artifacts"):
                artifact = json.loads(raw)
                snapshot = artifact.get("snapshot")
                if isinstance(snapshot, dict) and "snapshotSha256" in artifact:
                    self.assertNotIn("changedEntries", snapshot)
                    self.assertNotIn("changedEntriesTruncated", snapshot)
                    self.assertEqual(artifact["snapshotSha256"], workspace._sha(workspace._json(snapshot)))
                    self.assertEqual(digest, artifact["snapshotSha256"])
            result_rows = [json.loads(row[0]) for row in connection.execute(
                "SELECT result_json FROM attempts WHERE result_json IS NOT NULL")]
            self.assertTrue(any("workspaceSeal" in result.get("result", {}) for result in result_rows))
            for result in result_rows:
                seal = result.get("result", {}).get("workspaceSeal")
                if seal:
                    self.assertNotIn("changedEntries", seal["snapshot"])
                    self.assertNotIn("changedEntriesTruncated", seal["snapshot"])
            turn_refs = [json.loads(row[0]) for row in connection.execute(
                "SELECT sealed_artifacts_json FROM workflow_turns")]
            turn_input = connection.execute("SELECT manifest_json,manifest_sha256 FROM workflow_artifacts "
                                            "WHERE artifact_id='turn-input-fixture'").fetchone()
            self.assertEqual(json.loads(turn_input[0]), {"manifestSha256": "input-identity"})
            self.assertEqual(turn_input[1], "input-identity")
            self.assertTrue(any(refs for refs in turn_refs))
            for refs in turn_refs:
                for ref in refs:
                    self.assertIn(ref["snapshotSha256"], plan["identities"].values())
        for path in plan["physical"]:
            record = json.loads(path.read_bytes())
            self.assertNotIn("changedEntries", record["snapshot"])
            self.assertNotIn("changedEntriesTruncated", record["snapshot"])

    def test_partial_attempt_and_pending_file_without_pinned_artifact(self):
        with sqlite3.connect(self.state / "board.sqlite3") as connection:
            connection.row_factory = sqlite3.Row
            artifact = json.loads(connection.execute("SELECT manifest_json FROM workflow_artifacts "
                                                     "WHERE kind='output' LIMIT 1").fetchone()[0])
            attempt = connection.execute("SELECT attempt_id,result_json FROM attempts "
                                         "WHERE result_json IS NOT NULL LIMIT 1").fetchone()
            receipt = json.loads(attempt["result_json"])
            pending = json.loads(json.dumps(artifact))
            pending["snapshot"]["commit"] = "pre-pin-partial"
            pending["commit"] = "pre-pin-partial"
            pending["snapshotSha256"] = workspace._sha(workspace._json(pending["snapshot"]))
            directory = Path(artifact["diffPath"]).parent.parent / "orphan-partial"
            directory.mkdir()
            path = directory / "pending.json"
            path.write_bytes(b" \n" + workspace._json(pending) + b"\n")
            receipt_path = (self.state / "harnesses" / "command" / "micro-tasks" / "orphan-task"
                            / "attempts" / "orphan-attempt" / "result-receipt.json")
            receipt_path.parent.mkdir(parents=True)
            receipt_path.write_bytes(b"\n" + workspace._json({"result": {"partialWorkspaceSeal": pending}}) + b"\n")
            receipt.setdefault("result", {})["partialWorkspaceSeal"] = pending
            connection.execute("UPDATE attempts SET result_json=? WHERE attempt_id=?",
                               (json.dumps(receipt), attempt["attempt_id"]))
        old = path.read_bytes()
        old_receipt = receipt_path.read_bytes()
        plan, archive, _ = self._backup_and_apply()
        self.assertIn(path, plan["physical"])
        self.assertIn(receipt_path, plan["physical"])
        self.assertNotIn("changedEntries", json.loads(path.read_bytes())["snapshot"])
        self.assertNotIn("changedEntries", json.loads(receipt_path.read_bytes())["result"]["partialWorkspaceSeal"]["snapshot"])
        with sqlite3.connect(self.state / "board.sqlite3") as connection:
            receipt = json.loads(connection.execute("SELECT result_json FROM attempts WHERE attempt_id=?",
                                                    (attempt["attempt_id"],)).fetchone()[0])
        self.assertEqual(receipt["result"]["partialWorkspaceSeal"]["snapshotSha256"],
                         json.loads(path.read_bytes())["snapshotSha256"])
        installer.restore(self.state, archive)
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(receipt_path.read_bytes(), old_receipt)

    def test_nested_turn_event_and_command_copies_rebind_without_touching_opaque_text(self):
        with sqlite3.connect(self.state / "board.sqlite3") as connection:
            connection.row_factory = sqlite3.Row
            artifact = json.loads(connection.execute("SELECT manifest_json FROM workflow_artifacts "
                                                     "WHERE kind='output' LIMIT 1").fetchone()[0])
            old = artifact["snapshotSha256"]
            copy = {"workspaceSeal": artifact, "snapshotSha256": old,
                    "manifestSha256": old, "note": old}
            turn = connection.execute("SELECT turn_id,input_json,outcome_json,provenance_json,prompt_sha256 "
                                      "FROM workflow_turns LIMIT 1").fetchone()
            prompt = turn["prompt_sha256"]
            for column in ("input_json", "outcome_json", "provenance_json"):
                value = json.loads(turn[column]) if turn[column] else {}
                value["upgradeCopy"] = copy
                connection.execute(f"UPDATE workflow_turns SET {column}=? WHERE turn_id=?",
                                   (json.dumps(value), turn["turn_id"]))
            input_raw = connection.execute("SELECT input_json FROM workflow_turns WHERE turn_id=?",
                                           (turn["turn_id"],)).fetchone()[0]
            connection.execute("UPDATE workflow_turns SET input_sha256=? WHERE turn_id=?",
                               (workspace._sha(input_raw.encode()), turn["turn_id"]))
            event = connection.execute("SELECT seq,payload_json FROM events LIMIT 1").fetchone()
            payload = json.loads(event["payload_json"])
            payload["upgradeCopy"] = copy
            connection.execute("UPDATE events SET payload_json=? WHERE seq=?", (json.dumps(payload), event["seq"]))
            command = connection.execute("SELECT command_id,response_json FROM commands LIMIT 1").fetchone()
            response = json.loads(command["response_json"])
            response["upgradeCopy"] = copy
            connection.execute("UPDATE commands SET response_json=? WHERE command_id=?",
                               (json.dumps(response), command["command_id"]))
        plan, _, _ = self._backup_and_apply()
        new = plan["identities"][old]
        with sqlite3.connect(self.state / "board.sqlite3") as connection:
            connection.row_factory = sqlite3.Row
            turn_after = connection.execute("SELECT * FROM workflow_turns WHERE turn_id=?", (turn["turn_id"],)).fetchone()
            self.assertEqual(turn_after["prompt_sha256"], prompt)
            self.assertEqual(turn_after["input_sha256"], workspace._sha(turn_after["input_json"].encode()))
            raws = [turn_after[column] for column in ("input_json", "outcome_json", "provenance_json")]
            raws += [connection.execute("SELECT payload_json FROM events WHERE seq=?", (event["seq"],)).fetchone()[0],
                     connection.execute("SELECT response_json FROM commands WHERE command_id=?",
                                        (command["command_id"],)).fetchone()[0]]
        for raw in raws:
            nested = json.loads(raw)["upgradeCopy"]
            self.assertEqual(nested["snapshotSha256"], new)
            self.assertEqual(nested["manifestSha256"], new)
            self.assertEqual(nested["workspaceSeal"]["snapshotSha256"], new)
            self.assertNotIn("changedEntries", nested["workspaceSeal"]["snapshot"])
            self.assertNotIn("changedEntriesTruncated", nested["workspaceSeal"]["snapshot"])
            self.assertEqual(nested["note"], old)

    def test_rebound_turn_input_preserves_checkpoint_and_shared_refs_binding(self):
        with sqlite3.connect(self.state / "board.sqlite3") as connection:
            connection.row_factory = sqlite3.Row
            output = json.loads(connection.execute("SELECT manifest_json FROM workflow_artifacts "
                                                   "WHERE kind='output' LIMIT 1").fetchone()[0])
            row = connection.execute("SELECT artifact_id,turn_id,manifest_json FROM workflow_artifacts "
                                     "WHERE kind='shared-refs' LIMIT 1").fetchone()
            turn = connection.execute("SELECT input_json FROM workflow_turns WHERE turn_id=?",
                                      (row["turn_id"],)).fetchone()
            document = json.loads(turn["input_json"])
            document["context"]["artifacts"] = [{"manifestSha256": output["snapshotSha256"]}]
            old_input_sha = input_hash(document)
            connection.execute("UPDATE workflow_turns SET input_json=?,input_sha256=? WHERE turn_id=?",
                               (canonical_json(document), old_input_sha, row["turn_id"]))
            shared = json.loads(row["manifest_json"])
            shared["turnInputSha256"] = old_input_sha
            shared["manifestSha256"] = WorkflowCoordinator.shared_refs_record_digest(shared)
            connection.execute("UPDATE workflow_artifacts SET manifest_json=?,manifest_sha256=? WHERE artifact_id=?",
                               (json.dumps(shared), shared["manifestSha256"], row["artifact_id"]))
        checkpoint = {"version": 1, **{key: document[key] for key in
                      ("taskId", "attemptId", "generation", "turnId")},
                      "inputSha256": old_input_sha, "sessionId": "native-session", "nativeTurnId": "native-turn",
                      "nativeTurnStarted": True, "nativeTurnStatus": "completed", "bindingSaved": True, "eventSeq": 2}
        payload = {"nativeCheckpoint": checkpoint, "sessionId": "native-session", "nativeTurnId": "native-turn",
                   "processState": {"shutdownConfirmed": True}}
        self.assertIsNotNone(validated_checkpoint(payload, document))
        self._backup_and_apply()
        with sqlite3.connect(self.state / "board.sqlite3") as connection:
            after = json.loads(connection.execute("SELECT input_json FROM workflow_turns WHERE turn_id=?",
                                                  (row["turn_id"],)).fetchone()[0])
            shared_after = json.loads(connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE artifact_id=?",
                                                         (row["artifact_id"],)).fetchone()[0])
        with self.subTest(binding="native checkpoint"):
            self.assertIsNotNone(validated_checkpoint(payload, after))
        with self.subTest(binding="shared refs"):
            self.assertEqual(shared_after["turnInputSha256"], input_hash(after))

    def test_rollback_restores_exact_physical_bytes(self):
        candidate = upgrade.plan_schema_25_to_26(self.state)
        path = next(iter(candidate["physical"]))
        old = path.read_bytes()
        path.write_bytes(b" \n" + old + b"\n")
        plan, archive, _ = self._backup_and_apply()
        self.assertNotEqual(path.read_bytes(), b" \n" + old + b"\n")
        installer.restore(self.state, archive)
        self.assertEqual(path.read_bytes(), b" \n" + old + b"\n")
        with sqlite3.connect(self.state / "board.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], "25")

    def test_rejected_digest_leaves_every_physical_file_untouched(self):
        plan = upgrade.plan_schema_25_to_26(self.state)
        originals = {path: original for path, (original, _) in plan["physical"].items()}
        with sqlite3.connect(self.state / "board.sqlite3") as connection:
            connection.execute("UPDATE workflow_artifacts SET manifest_sha256='wrong' "
                               "WHERE artifact_id=(SELECT artifact_id FROM workflow_artifacts WHERE kind='output' LIMIT 1)")
        with self.assertRaises(BoardError) as caught:
            upgrade.plan_schema_25_to_26(self.state)
        self.assertEqual(caught.exception.code, "UPGRADE_INVALID_SEAL")
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)

    def test_linked_record_parent_rejected_before_writes(self):
        plan = upgrade.plan_schema_25_to_26(self.state)
        path = next(iter(plan["physical"]))
        directory = path.parent
        outside = self.root / "outside"
        directory.rename(outside)
        try:
            directory.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Directory links unavailable: {error}")
        originals = {file: file.read_bytes() for file in outside.iterdir() if file.is_file()}
        with self.assertRaises(BoardError):
            upgrade.plan_schema_25_to_26(self.state)
        self.assertEqual({file: file.read_bytes() for file in originals}, originals)
