"""Frozen schema-30 routing statistics survive the explicit upgrade."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from blackboard.store.upgrade_fixture import FIXTURE, load_fixture
from hey_my_buddy.blackboard.routing import evidence
from hey_my_buddy.blackboard.store.upgrade import upgrade_state
from hey_my_buddy.blackboard.store import upgrade


ORACLE = Path(__file__).resolve().parents[3] / "fixtures/upgrade-statistics.json"
SOURCE_COMMIT = "403c4d3b569fa9fc1084fce1abaaa35b7f26275f"
SOURCE_PATH = "src/hey_my_buddy/blackboard/routing/evidence.py"
MEDIANS = frozenset({"medianDeliverySeconds", "medianInputTokens",
                     "medianCachedInputTokens", "medianOutputTokens"})


def _buddies(connection):
    return [dict(row) for row in connection.execute(
        "SELECT buddy_id,harness,provider,model,effort FROM buddies ORDER BY buddy_id")]


def _write_oracle():
    """One-time capture from the exact pre-change reader, without copying it here."""
    source = subprocess.run(
        ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
        check=True, capture_output=True,
    ).stdout
    namespace = {"__name__": "hey_my_buddy.blackboard.routing._upgrade_statistics_oracle",
                 "__package__": "hey_my_buddy.blackboard.routing"}
    exec(compile(source, f"{SOURCE_COMMIT}:{SOURCE_PATH}", "exec"), namespace)
    with tempfile.TemporaryDirectory(prefix="upgrade-statistics-oracle-") as scratch:
        fixture = load_fixture(Path(scratch) / "state")
        with closing(sqlite3.connect(fixture["state"] / "board.sqlite3")) as connection:
            connection.row_factory = sqlite3.Row
            overall, infrastructure, by_domain, by_cell = namespace["_statistics"](connection)
            if by_domain or by_cell:
                raise AssertionError("Schema-30 fixture unexpectedly has domain statistics")
            facts = {}
            for buddy in _buddies(connection):
                kind = namespace["_key"](buddy, namespace["KIND"])
                identity = namespace["_key"](buddy, namespace["IDENTITY"])
                facts[buddy["buddy_id"]] = {
                    "overall": namespace["_statistics_fact"](overall[kind]),
                    "byDomain": {},
                    "byDomainTaskType": {},
                    "infrastructureFailures": infrastructure[identity],
                }
    fixture_hashes = {
        name: hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest()
        for name in ("board.sql", "catalog.json", "metadata.json",
                     "operations.json", "workspace-resources.json")
    }
    oracle = {
        "format": 1,
        "source": {
            "commit": SOURCE_COMMIT,
            "path": SOURCE_PATH,
            "functions": ["_statistics", "_statistics_fact"],
            "sha256": hashlib.sha256(source).hexdigest(),
        },
        "fixture": {
            "path": "tests/fixtures/upgrade-base",
            "sourceCommit": fixture["sourceCommit"],
            "sha256": fixture_hashes,
        },
        "preparationCommands": [
            "mkdir -p tmp/upgrade-statistics-oracle",
            "UV_PROJECT_ENVIRONMENT=tmp/upgrade-statistics-oracle/venv "
            "UV_CACHE_DIR=tmp/upgrade-statistics-oracle/uv-cache "
            "TMPDIR=tmp/upgrade-statistics-oracle "
            "uv sync --frozen --no-install-project",
        ],
        "generationCommand": (
            "env -u HMB_STATE_DIR -u HMB_RUNTIME_ROOT -u HMB_RUNTIME "
            "-u HMB_RUNTIME_IDENTITY -u HMB_AGENT_CREDENTIAL_FILE "
            "-u HMB_AGENT_CREDENTIAL_FD -u VIRTUAL_ENV "
            "-u UV_PROJECT_ENVIRONMENT TMPDIR=tmp/upgrade-statistics-oracle "
            "PYTHONPATH=src:tests/python "
            "tmp/upgrade-statistics-oracle/venv/bin/python "
            "tests/python/blackboard/store/test_upgrade_statistics.py --write-oracle"
        ),
        "buddies": facts,
    }
    ORACLE.write_text(json.dumps(oracle, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                      encoding="utf-8")


class UpgradeStatisticsTests(unittest.TestCase):
    def test_upgrade_preserves_negative_duration_from_base_wall_clock_rollback(self):
        with tempfile.TemporaryDirectory(prefix="upgrade-statistics-clock-") as scratch:
            fixture = load_fixture(Path(scratch) / "state")
            with closing(sqlite3.connect(fixture["state"] / "board.sqlite3")) as connection:
                attempt = connection.execute("SELECT a.attempt_id,a.started_at FROM attempts a JOIN workflow_runs r"
                    " ON r.final_attempt_id=a.attempt_id WHERE r.run_id=?", (fixture["stableIds"]["accepted"],)).fetchone()
                earlier = datetime.fromisoformat(attempt[1].replace("Z", "+00:00")) - timedelta(seconds=10)
                connection.execute("UPDATE attempts SET finished_at=? WHERE attempt_id=?",
                                   (earlier.isoformat().replace("+00:00", "Z"), attempt[0]))
                connection.commit()
                upgrade_state(fixture["state"])
                self.assertEqual(connection.execute("SELECT SUM(count) FROM kind_statistics_buckets"
                    " WHERE metric='delivery_seconds' AND sign=-1").fetchone()[0], 1)

    def test_failed_backfill_rolls_back_schema_marker_and_all_counts(self):
        with tempfile.TemporaryDirectory(prefix="upgrade-statistics-rollback-") as scratch:
            fixture = load_fixture(Path(scratch) / "state")
            with closing(sqlite3.connect(fixture["state"] / "board.sqlite3")) as connection:
                before = list(connection.iterdump())
                original = upgrade.backfill

                def fail_after_writes(writer):
                    original(writer)
                    self.assertGreater(writer.execute("SELECT SUM(samples) FROM kind_statistics").fetchone()[0], 0)
                    self.assertGreater(writer.execute("SELECT SUM(count) FROM buddy_infrastructure_failures").fetchone()[0], 0)
                    raise RuntimeError("after-backfill")

                with mock.patch.object(upgrade, "backfill", side_effect=fail_after_writes):
                    with self.assertRaisesRegex(RuntimeError, "after-backfill"):
                        upgrade_state(fixture["state"])
                self.assertEqual(list(connection.iterdump()), before)
            upgrade_state(fixture["state"])

    def test_frozen_reader_facts_survive_schema_upgrade(self):
        oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
        self.assertEqual(oracle["format"], 1)
        for name, digest in oracle["fixture"]["sha256"].items():
            self.assertEqual(hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest(), digest)
        with tempfile.TemporaryDirectory(prefix="upgrade-statistics-") as scratch:
            fixture = load_fixture(Path(scratch) / "state")
            self.assertEqual(fixture["sourceCommit"], oracle["fixture"]["sourceCommit"])
            upgrade_state(fixture["state"])
            with closing(sqlite3.connect(fixture["state"] / "board.sqlite3")) as connection:
                connection.row_factory = sqlite3.Row
                actual = evidence.domain_statistics(connection, _buddies(connection))
        self._assert_facts(actual, oracle["buddies"])

    def _assert_facts(self, actual, expected, path=()):
        if isinstance(expected, dict):
            self.assertIsInstance(actual, dict, path)
            self.assertEqual(set(actual), set(expected), path)
            for key, value in expected.items():
                self._assert_facts(actual[key], value, (*path, key))
        elif path[-1] in MEDIANS and expected is not None and expected != 0:
            self.assertIsInstance(actual, (int, float), path)
            radius = math.sqrt(1.2)
            self.assertGreaterEqual(actual, expected / radius, path)
            self.assertLessEqual(actual, expected * radius, path)
        else:
            self.assertEqual(actual, expected, path)


if __name__ == "__main__":
    if sys.argv[1:] == ["--write-oracle"]:
        _write_oracle()
    else:
        unittest.main()
