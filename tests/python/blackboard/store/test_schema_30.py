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
from hey_my_buddy.paths import native_path
from hey_my_buddy.protocol.usage import normalize_token_usage


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

    def test_fixture_converts_base_rejections_and_preserves_sealed_evidence(self):
        files = {path.relative_to(native_path(self.state)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in native_path(self.state).rglob("*") if path.is_file() and not path.name.startswith("board.sqlite3")}
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            old_results = {key: json.loads(raw) for key, raw in connection.execute(
                "SELECT attempt_id,result_json FROM attempts WHERE result_json IS NOT NULL")}
            old_usage = dict(connection.execute("SELECT attempt_id,token_usage_json FROM attempts"))
            turns = list(connection.execute("SELECT turn_id,input_json FROM workflow_turns"))
            snapshots = list(connection.execute("SELECT * FROM router_requests ORDER BY decision_id,round"))
            outputs = list(connection.execute("SELECT decision_id,output_json FROM decision_requests ORDER BY decision_id"))
            before = backup.database_snapshot(connection)
        codes = {(item.get("result") or {}).get("quotaFailure", {}).get("code") for item in old_results.values()}
        self.assertEqual(codes - {None}, {"quota-exceeded", "rate-limited"})
        result = schema_upgrade.upgrade_state(self.state)
        self.assertEqual((result["sourceSchema"], result["targetSchema"]), (29, 30))
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(list(connection.execute("SELECT turn_id,input_json FROM workflow_turns")), turns)
            self.assertEqual(list(connection.execute("SELECT * FROM router_requests ORDER BY decision_id,round")), snapshots)
            self.assertEqual(list(connection.execute("SELECT decision_id,output_json FROM decision_requests ORDER BY decision_id")), outputs)
            new_results = {key: json.loads(raw) for key, raw in connection.execute(
                "SELECT attempt_id,result_json FROM attempts WHERE result_json IS NOT NULL")}
            for key, old in old_results.items():
                source = old.get("result") or {}
                target = new_results[key].get("result") or {}
                self.assertNotIn("quotaFailure", target)
                if "quotaFailure" in source:
                    self.assertEqual(target["providerRejection"], source["quotaFailure"])
                if old_usage[key] is not None:
                    self.assertEqual(target["tokenUsage"], json.loads(old_usage[key]))
                self.assertEqual(target.get("nativeFailure"), source.get("nativeFailure"))
                for field in ("error", "exitCode", "signal", "logPaths"):
                    self.assertEqual(new_results[key].get(field), old.get(field))
            after = backup.database_snapshot(connection)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(attempts)")}
            self.assertFalse(set(schema_upgrade.REMOVED_ATTEMPT_COLUMNS) & columns)
        self.assertEqual(after["tables"], before["tables"])
        touched = {"meta", "attempts", "decision_requests"}
        for table, fingerprint in before["fingerprints"].items():
            if table not in touched:
                self.assertEqual(after["fingerprints"][table], fingerprint, table)
        self.assertEqual({path.relative_to(native_path(self.state)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in native_path(self.state).rglob("*") if path.is_file() and not path.name.startswith("board.sqlite3")}, files)

    def test_mutable_routing_basis_drops_verified_counts_and_adds_empty_overload_list(self):
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            before = {key: (json.loads(raw), fingerprint) for key, raw, fingerprint in connection.execute(
                "SELECT decision_id,requested_json,input_fingerprint FROM decision_requests")}
        schema_upgrade.upgrade_state(self.state)
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            converted = 0
            for decision_id, raw, fingerprint in connection.execute(
                    "SELECT decision_id,requested_json,input_fingerprint FROM decision_requests"):
                original, old_fingerprint = before[decision_id]
                self.assertEqual(fingerprint, old_fingerprint)
                basis = original.get("routingBasis")
                if basis is not None:
                    self.assertEqual(basis.pop("excludedCount"), len(basis["excludedBuddies"]))
                    if "coolingCount" in basis:
                        self.assertEqual(basis.pop("coolingCount"), len(basis["coolingBuddies"]))
                    else:
                        basis["coolingBuddies"] = []
                    basis["overloadedModelServices"] = []
                    converted += 1
                self.assertEqual(json.loads(raw), original)
            self.assertGreater(converted, 0)

    def test_pre_cooling_stock_becomes_the_current_form(self):
        derived = self.fixture["derivedForms"]
        self.assertEqual([row["name"] for row in derived], ["pre-provider-cooling-routing-basis"])
        decision_id = derived[0]["row"]["decisionId"]
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            original = json.loads(connection.execute("SELECT requested_json FROM decision_requests WHERE decision_id=?",
                                                     (decision_id,)).fetchone()[0])
            self.assertEqual(set(original["routingBasis"]), {"candidateCount", "excludedCount", "excludedBuddies"})
            forms = {frozenset(json.loads(raw)["routingBasis"]) for raw, in connection.execute("SELECT requested_json FROM decision_requests")}
            self.assertEqual(forms, {frozenset({"candidateCount", "excludedCount", "excludedBuddies"}),
                                     frozenset({"candidateCount", "excludedCount", "excludedBuddies", "coolingCount", "coolingBuddies"})})
        schema_upgrade.upgrade_state(self.state)
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            upgraded = json.loads(connection.execute("SELECT requested_json FROM decision_requests WHERE decision_id=?",
                                                     (decision_id,)).fetchone()[0])
        self.assertEqual(upgraded["routingBasis"], {
            "candidateCount": original["routingBasis"]["candidateCount"],
            "excludedBuddies": original["routingBasis"]["excludedBuddies"],
            "coolingBuddies": [], "overloadedModelServices": []})

    def test_incomplete_or_unknown_routing_form_refuses_the_upgrade_without_changes(self):
        required = {"candidateCount": 2, "excludedCount": 0, "excludedBuddies": []}
        cooled = {**required, "coolingCount": 0, "coolingBuddies": []}
        cases = [(None, "routingBasis"),
                 ({**required, "coolingCount": 0}, "routingBasis.coolingBuddies"),
                 ({**required, "coolingBuddies": []}, "routingBasis.coolingCount"),
                 ({**cooled, "extra": []}, "routingBasis.extra"),
                 ({**cooled, "excludedBuddies": None}, "routingBasis.excludedBuddies"),
                 ({**cooled, "coolingBuddies": None}, "routingBasis.coolingBuddies"),
                 ({**cooled, "excludedCount": "0"}, "routingBasis.excludedCount"),
                 ({**cooled, "candidateCount": None}, "routingBasis.candidateCount"),
                 *[({key: value for key, value in required.items() if key != missing}, "routingBasis." + missing)
                   for missing in required]]
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            decision_id, raw = connection.execute("SELECT decision_id,requested_json FROM decision_requests LIMIT 1").fetchone()
            for basis, field in cases:
                with self.subTest(field=field):
                    request = json.loads(raw)
                    request["routingBasis"] = basis
                    connection.execute("UPDATE decision_requests SET requested_json=? WHERE decision_id=?", (json.dumps(request), decision_id))
                    connection.commit()
                    before = self.snapshot()
                    with self.assertRaises(BoardError) as caught:
                        schema_upgrade.upgrade_state(self.state)
                    self.assertEqual(caught.exception.code, "UPGRADE_INVALID_BOARD")
                    self.assertEqual(caught.exception.details, {"table": "decision_requests", "row": decision_id, "field": field})
                    self.assertEqual(self.snapshot(), before)
            connection.execute("UPDATE decision_requests SET requested_json=? WHERE decision_id=?", (raw, decision_id))
            connection.commit()

    def test_any_mismatched_attempt_copy_refuses_the_upgrade_without_changes(self):
        corruptions = {"exit_code": 99, "signal": "SIGINT", "log_paths": '{"stderr":"unexpected"}',
                       "error": "a different error", "token_usage_json": '{"inputTokens":999}'}
        for field, value in corruptions.items():
            with self.subTest(field=field), closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
                condition = "token_usage_json IS NOT NULL" if field == "token_usage_json" else "result_json IS NOT NULL"
                attempt_id, original = connection.execute(
                    f'SELECT attempt_id,"{field}" FROM attempts WHERE {condition} ORDER BY rowid DESC LIMIT 1').fetchone()
                connection.execute(f'UPDATE attempts SET "{field}"=? WHERE attempt_id=?', (value, attempt_id))
                connection.commit()
                before = self.snapshot()
                with self.assertRaises(BoardError) as caught:
                    schema_upgrade.upgrade_state(self.state)
                self.assertEqual(caught.exception.code, "UPGRADE_INVALID_BOARD")
                self.assertEqual(caught.exception.details, {"table": "attempts", "row": attempt_id, "field": field})
                self.assertEqual(self.snapshot(), before)
                connection.execute(f'UPDATE attempts SET "{field}"=? WHERE attempt_id=?', (original, attempt_id))
                connection.commit()

    def test_attempt_without_a_result_cannot_lose_an_error(self):
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            attempt_id = connection.execute("SELECT attempt_id FROM attempts ORDER BY rowid DESC LIMIT 1").fetchone()[0]
            connection.execute("UPDATE attempts SET result_json=NULL,token_usage_json=NULL,exit_code=NULL,signal=NULL,"
                               "log_paths='{}',error='unrecorded error' WHERE attempt_id=?", (attempt_id,))
            connection.commit()
        before = self.snapshot()
        with self.assertRaises(BoardError) as caught:
            schema_upgrade.upgrade_state(self.state)
        self.assertEqual(caught.exception.code, "UPGRADE_INVALID_BOARD")
        self.assertEqual(caught.exception.details, {"table": "attempts", "row": attempt_id, "field": "error"})
        self.assertEqual(self.snapshot(), before)

    def test_mismatched_routing_counts_refuse_the_upgrade_without_changes(self):
        for field in ("excludedCount", "coolingCount"):
            with self.subTest(field=field), closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
                decision_id, raw = connection.execute(
                    "SELECT decision_id,requested_json FROM decision_requests WHERE json_type(requested_json,'$.routingBasis.coolingCount')='integer' LIMIT 1").fetchone()
                value = json.loads(raw)
                value["routingBasis"][field] += 1
                connection.execute("UPDATE decision_requests SET requested_json=? WHERE decision_id=?", (json.dumps(value), decision_id))
                connection.commit()
                before = self.snapshot()
                with self.assertRaises(BoardError) as caught:
                    schema_upgrade.upgrade_state(self.state)
                self.assertEqual(caught.exception.code, "UPGRADE_INVALID_BOARD")
                self.assertEqual(caught.exception.details, {"table": "decision_requests", "row": decision_id,
                                                          "field": "routingBasis." + field})
                self.assertEqual(self.snapshot(), before)
                connection.execute("UPDATE decision_requests SET requested_json=? WHERE decision_id=?", (raw, decision_id))
                connection.commit()

    def test_canonical_usage_column_is_merged_when_result_has_no_usage(self):
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            attempt_id, raw, usage = connection.execute(
                "SELECT attempt_id,result_json,token_usage_json FROM attempts WHERE token_usage_json IS NOT NULL LIMIT 1").fetchone()
            value = json.loads(raw)
            del value["result"]["tokenUsage"]
            connection.execute("UPDATE attempts SET result_json=? WHERE attempt_id=?", (json.dumps(value), attempt_id))
            connection.commit()
        schema_upgrade.upgrade_state(self.state)
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            result = json.loads(connection.execute("SELECT result_json FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()[0])
        self.assertEqual(result["result"]["tokenUsage"], json.loads(usage))

    def test_native_usage_is_compared_with_the_bases_normalized_column(self):
        samples = ({"inputTokens": 100, "outputTokens": 3},
                   {"inputTokens": 100, "outputTokens": 3, "cacheReadTokens": 20, "cacheWriteTokens": 5,
                    "inputBasis": "excludes-cached", "source": "dsh/native-usage"})
        for index, usage in enumerate(samples):
            with self.subTest(usage=usage):
                state = load_fixture(self.root / f"native-usage-{index}")["state"]
                with closing(sqlite3.connect(state / "board.sqlite3")) as connection:
                    attempt_id, raw = connection.execute(
                        "SELECT attempt_id,result_json FROM attempts WHERE token_usage_json IS NOT NULL LIMIT 1").fetchone()
                    result = json.loads(raw)
                    result["result"]["tokenUsage"] = usage
                    expected = normalize_token_usage(usage)
                    connection.execute("UPDATE attempts SET result_json=?,token_usage_json=? WHERE attempt_id=?",
                                       (json.dumps(result), json.dumps(expected), attempt_id))
                    connection.commit()
                schema_upgrade.upgrade_state(state)
                with closing(sqlite3.connect(state / "board.sqlite3")) as connection:
                    converted = json.loads(connection.execute("SELECT result_json FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()[0])
                self.assertEqual(converted["result"]["tokenUsage"], expected)

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
        run_id = self.fixture["edgeStock"]["rejected-turn"]["runId"]
        view = store.workflow.get({"runId": run_id, "includeAudit": True})
        self.assertNotIn("quotaFailure", view)
        self.assertEqual(view["providerRejection"]["code"], "rate-limited")


if __name__ == "__main__":
    unittest.main()
