"""Upgrade the fixed base stock without changing its sealed evidence."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from temporary import TemporaryDirectory
import unittest
from unittest.mock import patch

from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import backup, upgrade as schema_upgrade
from hey_my_buddy.blackboard.store.db import Database, SCHEMA_VERSION
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade as installation


class Schema30Tests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="schema-30-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = load_fixture(self.root / "state")
        self.state = self.fixture["state"]

    def snapshot(self):
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            return backup.database_snapshot(connection)

    def test_fixture_converts_all_rejection_codes_and_preserves_frozen_files(self):
        files = {path.relative_to(self.state): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in self.state.rglob("*") if path.is_file() and not path.name.startswith("board.sqlite3")}
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            old_results = {key: json.loads(raw) for key, raw in connection.execute(
                "SELECT attempt_id,result_json FROM attempts WHERE result_json IS NOT NULL")}
            old_usage = dict(connection.execute("SELECT attempt_id,token_usage_json FROM attempts"))
            turns = list(connection.execute("SELECT turn_id,input_json FROM workflow_turns"))
            before = backup.database_snapshot(connection)
        codes = {(item.get("result") or {}).get("quotaFailure", {}).get("code") for item in old_results.values()}
        self.assertTrue({"quota-exceeded", "rate-limited", "model-overloaded"} <= codes)
        result = schema_upgrade.upgrade_state(self.state)
        self.assertEqual((result["sourceSchema"], result["targetSchema"]), (29, 30))
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(list(connection.execute("SELECT turn_id,input_json FROM workflow_turns")), turns)
            new_results = {key: json.loads(raw) for key, raw in connection.execute(
                "SELECT attempt_id,result_json FROM attempts WHERE result_json IS NOT NULL")}
            for key, old in old_results.items():
                source = old.get("result") or {}
                target = new_results[key].get("result") or {}
                self.assertNotIn("quotaFailure", target)
                if "quotaFailure" in source:
                    expected = {**source["quotaFailure"]}
                    if expected["code"] == "model-overloaded":
                        expected["code"] = "overloaded"
                    self.assertEqual(target["providerRejection"], expected)
                if old_usage[key] is not None:
                    self.assertEqual(target["tokenUsage"], json.loads(old_usage[key]))
                self.assertEqual(target.get("nativeFailure"), source.get("nativeFailure"))
                for field in ("error", "exitCode", "signal", "logPaths"):
                    self.assertEqual(new_results[key].get(field), old.get(field))
            after = backup.database_snapshot(connection)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(attempts)")}
            self.assertFalse(set(schema_upgrade.REMOVED_ATTEMPT_COLUMNS) & columns)
        self.assertEqual(after["tables"], before["tables"])
        touched = {"meta", "attempts", "events", "decision_requests", "router_requests"}
        for table, fingerprint in before["fingerprints"].items():
            if table not in touched:
                self.assertEqual(after["fingerprints"][table], fingerprint, table)
        self.assertEqual({path.relative_to(self.state): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in self.state.rglob("*") if path.is_file() and not path.name.startswith("board.sqlite3")}, files)

    def test_mixed_overload_basis_becomes_one_model_service_and_known_user_exclusions(self):
        decision_id = self.fixture["providerRejectionStock"]["routeDecisionId"]
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            before = json.loads(connection.execute("SELECT requested_json FROM decision_requests WHERE decision_id=?",
                                                   (decision_id,)).fetchone()[0])
            self.assertEqual(len(before["excludedModelServices"]), 1)
            self.assertTrue(any(item.get("reason") == "model-overloaded" for item in before["routingBasis"]["excludedBuddies"]))
            fingerprint = connection.execute("SELECT input_fingerprint FROM decision_requests WHERE decision_id=?", (decision_id,)).fetchone()[0]
        schema_upgrade.upgrade_state(self.state)
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            after = json.loads(connection.execute("SELECT requested_json FROM decision_requests WHERE decision_id=?",
                                                  (decision_id,)).fetchone()[0])
            self.assertEqual(connection.execute("SELECT input_fingerprint FROM decision_requests WHERE decision_id=?", (decision_id,)).fetchone()[0], fingerprint)
            basis = after["routingBasis"]
            self.assertNotIn("excludedModelServices", after)
            self.assertNotIn("excludedCount", basis)
            self.assertNotIn("coolingCount", basis)
            self.assertEqual(basis["candidateCount"], before["routingBasis"]["candidateCount"])
            self.assertEqual(basis["excludedBuddies"], [item for item in before["routingBasis"]["excludedBuddies"] if "reason" not in item])
            self.assertEqual(basis["overloadedModelServices"], [{key: before["excludedModelServices"][0][key]
                for key in ("harness", "provider", "model", "nativeCode", "turnId")}])
            snapshot = json.loads(connection.execute("SELECT snapshot_json FROM router_requests WHERE decision_id=?", (decision_id,)).fetchone()[0])
            self.assertEqual(snapshot["baseInput"]["routingBasis"], basis)
            event = json.loads(connection.execute("SELECT payload_json FROM events WHERE kind='workflow.auto_continued' ORDER BY seq DESC LIMIT 1").fetchone()[0])
            self.assertEqual(event["reason"], "overloaded")

    def test_upgraded_schema_exactly_matches_a_new_board(self):
        schema_upgrade.upgrade_state(self.state)
        fresh = Database(self.root / "fresh")
        self.addCleanup(fresh.close)
        fresh.initialize()
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection, fresh.read() as baseline:
            query = "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
            self.assertEqual(list(connection.execute(query)), [tuple(row) for row in baseline.execute(query)])

    def test_upgrade_rolls_back_data_and_ddl_when_conversion_fails(self):
        before = self.snapshot()
        real_upgrade = schema_upgrade.upgrade_schema_29_to_30

        def fail_after_rebuild(connection):
            real_upgrade(connection)
            raise RuntimeError("injected failure after table rebuild")

        with patch.object(schema_upgrade, "upgrade_schema_29_to_30", side_effect=fail_after_rebuild):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                schema_upgrade.upgrade_state(self.state)
        self.assertEqual(self.snapshot(), before)
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], "29")
            self.assertTrue(set(schema_upgrade.REMOVED_ATTEMPT_COLUMNS) <= {row[1] for row in connection.execute("PRAGMA table_info(attempts)")})

    def test_installation_verifies_expected_upgrade_and_restores_the_old_board(self):
        before = installation.idle_snapshot(self.state)
        self.assertEqual(installation.preflight(self.state, contract_changed=True)["schema"], 29)
        with self.assertRaises(BoardError):
            installation.preflight(self.state, contract_changed=False)
        saved = backup.create(self.state, runtime_identity=None, plugin_commit=None, contract_version="0.53.0")
        self.assertEqual(backup.verify(saved["path"])["schema"], 29)
        schema_upgrade.upgrade_state(self.state)
        expected = installation.idle_snapshot(self.state)
        target = self.root / "target"
        health = {"runtimeContentId": target.name, "runtimeStable": True, "schemaVersion": SCHEMA_VERSION}
        self.assertTrue(installation.verify_started(self.state, target, health, expected)["retainedDataFingerprints"])
        installation.restore(self.state, Path(saved["path"]))
        self.assertEqual(installation.idle_snapshot(self.state), before)
        health["schemaVersion"] = 29
        self.assertTrue(installation.verify_started(self.state, target, health, before)["retainedDataFingerprints"])

    def test_service_views_read_the_converted_result_without_old_columns(self):
        schema_upgrade.upgrade_state(self.state)
        store = BoardStore(self.state)
        self.addCleanup(store.db.close)
        store.initialize()
        for key in ("rejected-turn", "rejected-seal"):
            run_id = self.fixture["providerRejectionStock"][key]["runId"]
            view = store.workflow.get({"runId": run_id, "includeAudit": True})
            self.assertNotIn("quotaFailure", view)
            self.assertEqual(view["providerRejection"]["code"], "overloaded" if key == "rejected-seal" else "rate-limited")


if __name__ == "__main__":
    unittest.main()
