"""CI shard allocation accounts for the check runner's parallel file order."""
from contextlib import chdir, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[3] / ".github/select-test-shard.py"
SPEC = importlib.util.spec_from_file_location("ci_shards", SCRIPT)
sharding = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sharding)


class ShardSelectionTests(unittest.TestCase):
    def split(self, costs, count, *, delays=None, slowdown=1, separate=()):
        with tempfile.TemporaryDirectory(prefix="ci-shards-") as raw:
            root = Path(raw)
            (root / ".github").mkdir()
            (root / "tests/python").mkdir(parents=True)
            durations = {}
            for index, seconds in enumerate(costs):
                module = f"test_{index:02}"
                (root / f"tests/python/{module}.py").touch()
                durations[module] = seconds
            (root / ".github/check-durations.json").write_text(
                json.dumps({"fixture": durations}), encoding="utf-8")
            manifests = []
            with (chdir(root), redirect_stdout(io.StringIO()),
                  patch.dict(os.environ, {"CHECK_OS": "fixture", "CHECK_SHARDS": str(count),
                                          "CHECK_WORKERS": "2",
                                          "CHECK_START_DELAYS": json.dumps(delays or [0] * count),
                                          "CHECK_SLOWDOWN": str(slowdown),
                                          "CHECK_SEPARATE_MODULES": json.dumps(list(separate)),
                                          "RUNNER_TEMP": str(root), "GITHUB_ENV": str(root / "environment")}),
                  patch.object(sharding.os, "cpu_count", side_effect=AssertionError(
                      "shard allocation must use the workflow's shared worker count"))):
                for number in range(1, count + 1):
                    os.environ["CHECK_SHARD"] = str(number)
                    sharding.main()
                    manifests.append(json.loads((root / "logs/python-shard.json").read_text(encoding="utf-8")))
            return manifests

    def test_per_system_shard_counts_cover_every_file_exactly_once(self):
        costs = [6, 5, 6, 6, 4, 2, 10, 4, 1, 1]
        expected = [f"tests/python/test_{index:02}.py" for index in range(len(costs))]
        for count in (1, 2, 5, 8, 9):
            with self.subTest(shards=count):
                manifests = self.split(costs, count)
                self.assertEqual(sorted(path for item in manifests for path in item["files"]), expected)
                self.assertTrue(all(item["files"] and item["shards"] == count for item in manifests))

    def test_alphabetical_file_execution_has_no_avoidable_tail(self):
        # Balancing only total seconds produces a 16-second tail here. Two
        # workers per shard can finish these same files in 12 seconds.
        costs = [6, 5, 6, 6, 4, 2, 10, 4, 1, 1]
        for manifest in self.split(costs, 2):
            lanes = [0, 0]
            for path in sorted(manifest["files"]):
                worker = lanes.index(min(lanes))
                lanes[worker] += costs[int(Path(path).stem.removeprefix("test_"))]
            self.assertLessEqual(max(lanes), 12)
            self.assertEqual(manifest["estimated-wall-seconds"], max(lanes))
            self.assertEqual(manifest["workers"], 2)

    def test_queued_shards_get_less_work_before_a_slow_runner_extends_the_tail(self):
        # Two jobs start immediately; two more get their slots ten seconds
        # later. Equal-size shards finish at 28s on a 1.5x slower runner.
        manifests = self.split([6] * 12, 4, delays=[0, 0, 10, 10], slowdown=1.5)
        self.assertEqual(len({path for item in manifests for path in item["files"]}), 12)
        for manifest in manifests:
            lanes = [0, 0]
            for _path in manifest["files"]:
                lanes[lanes.index(min(lanes))] += 6
            finish = manifest["planned-delay-seconds"] + 1.5 * max(lanes)
            self.assertLessEqual(finish, 19)
            self.assertEqual(manifest["estimated-finish-seconds"], finish)

    def test_repeat_contention_cohort_is_placed_on_distinct_runners(self):
        separate = ("test_00", "test_01", "test_04", "test_07")
        manifests = self.split([12, 10, 8, 7, 6, 5, 4, 3, 2, 1], 4, separate=separate)
        for manifest in manifests:
            selected = {Path(path).stem for path in manifest["files"]}
            self.assertEqual(len(selected & set(separate)), 1)
