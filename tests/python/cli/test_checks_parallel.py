"""The parallel check runner runs what discovery would run and never hides a failure."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hey_my_buddy.cli import checks

REPOSITORY = Path(__file__).resolve().parents[3]

#: Prints every test id discovery loads and every id the per-file runner would load.
LOADED_IDS = """
import json, sys, unittest

def ids(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from ids(item)
        else:
            yield item.id()

discovered = sorted(ids(unittest.TestLoader().discover(sys.argv[1])))
per_file = sorted(
    name for module in sys.argv[2:] for name in ids(unittest.TestLoader().loadTestsFromName(module))
)
print(json.dumps({"discovered": discovered, "perFile": per_file}))
"""


def wait_until(predicate, timeout: float = 30.0, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def loaded_ids(tests: Path, modules: list[str], environment: dict) -> dict:
    completed = subprocess.run(
        [sys.executable, "-c", LOADED_IDS, str(tests), *modules],
        cwd=str(tests),
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr[-4000:])
    return json.loads(completed.stdout.splitlines()[-1])


class ScheduledModulesTests(unittest.TestCase):
    """Per-file scheduling covers exactly the tests ``unittest discover`` loads."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="checks-modules-"))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

    def test_this_checkout_schedules_exactly_what_discovery_loads(self):
        modules = checks.python_test_modules(REPOSITORY)
        environment = checks.child_environment(REPOSITORY, self.directory)
        (self.directory / "tmp").mkdir()
        loaded = loaded_ids(REPOSITORY / "tests" / "python", modules, environment)
        self.assertEqual(loaded["perFile"], loaded["discovered"])
        self.assertEqual(len(set(loaded["perFile"])), len(loaded["perFile"]), "a test is scheduled twice")
        self.assertFalse([name for name in loaded["discovered"] if "_FailedTest" in name])
        self.assertIn(f"{Path(__file__).parent.stem}.{Path(__file__).stem}.{type(self).__name__}.{self._testMethodName}", loaded["perFile"])

    def test_scheduling_covers_every_test_file_on_disk(self):
        """The raw filesystem is the independent full set, not discovery itself.

        Both ``unittest discover`` and ``python_test_modules`` descend into packages
        only, so they miss the same file together when a directory between it and
        ``tests/python`` lacks an ``__init__.py``. This guard compares discovery with
        a plain walk of the whole tree instead, and names every missed file.
        """
        top = REPOSITORY / "tests" / "python"
        on_disk: set[str] = set()
        for directory, _, files in os.walk(top):
            relative = Path(directory).relative_to(top)
            for name in files:
                if name.startswith("test") and name.endswith(".py"):
                    on_disk.add(".".join((*relative.parts, name.removesuffix(".py"))))
        scheduled = set(checks.python_test_modules(REPOSITORY))
        missing = sorted(on_disk - scheduled)
        self.assertFalse(missing, "test files on disk are never scheduled: " + ", ".join(missing))
        unexpected = sorted(scheduled - on_disk)
        self.assertFalse(unexpected, "scheduled modules that are not test files on disk: " + ", ".join(unexpected))

    def test_nested_packages_are_scheduled_exactly_as_discovery_descends(self):
        tests = self.directory / "tests" / "python"
        case = "import unittest\nclass Case(unittest.TestCase):\n    def test_runs(self):\n        pass\n"
        for relative in (
            "test_top.py",
            "nested/__init__.py",
            "nested/test_inner.py",
            "nested/deeper/__init__.py",
            "nested/deeper/test_deep.py",
            "nested/helper.py",
            "plain/test_unreachable.py",
            "plain/inside/__init__.py",
            "plain/inside/test_unreachable_too.py",
        ):
            path = tests / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("" if path.name == "__init__.py" else case)
        modules = checks.python_test_modules(self.directory)
        self.assertEqual(modules, ["nested.deeper.test_deep", "nested.test_inner", "test_top"])
        environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        loaded = loaded_ids(tests, modules, environment)
        self.assertEqual(loaded["perFile"], loaded["discovered"])
        self.assertEqual(len(loaded["discovered"]), 3)

    def test_the_slowest_first_hint_reorders_without_dropping_or_adding(self):
        modules = checks.python_test_modules(REPOSITORY)
        scheduled = checks._scheduled_test_modules(modules)
        self.assertEqual(sorted(scheduled), modules)
        self.assertEqual(len(set(scheduled)), len(scheduled))
        hinted = [name for name in checks.SLOWEST_FILES_FIRST if name in modules]
        self.assertEqual(scheduled[: len(hinted)], hinted)
        self.assertEqual(checks._scheduled_test_modules(["test_b", "test_a"]), ["test_a", "test_b"])


class ResolveJobsTests(unittest.TestCase):
    def test_the_flag_wins_over_the_environment_over_the_default(self):
        with patch.dict(os.environ, {"BUDDY_CHECKS_JOBS": "5"}):
            self.assertEqual(checks.resolve_jobs([]), 5)
            self.assertEqual(checks.resolve_jobs(["--jobs", "2"]), 2)
            self.assertEqual(checks.resolve_jobs(["--jobs=1"]), 1)
        environment = {key: value for key, value in os.environ.items() if key != "BUDDY_CHECKS_JOBS"}
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(checks.resolve_jobs([]), checks.default_jobs())
        self.assertGreaterEqual(checks.default_jobs(), 1)
        self.assertLessEqual(checks.default_jobs(), checks.MAX_DEFAULT_JOBS)

    def test_an_unusable_value_is_refused(self):
        for arguments in (["--jobs"], ["--jobs", "0"], ["--jobs", "-1"], ["--jobs", "many"], ["--job", "2"], ["2"]):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                checks.resolve_jobs(arguments)
        with patch.dict(os.environ, {"BUDDY_CHECKS_JOBS": "none"}), self.assertRaises(SystemExit):
            checks.resolve_jobs([])


class RunnerTestCase(unittest.TestCase):
    """A private root, importable fixture modules and a stand-in Node binary."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="checks-parallel-"))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.modules = self.directory / "modules"
        self.modules.mkdir()
        self.markers = self.directory / "markers"
        self.markers.mkdir()
        self.private_root = self.directory / "root"
        self.private_root.mkdir()

    def module(self, name: str, body: str) -> str:
        (self.modules / f"{name}.py").write_text(textwrap.dedent(body))
        return name

    def environment(self, node_exit: int = 0, node_summary: str = "") -> dict:
        node = self.directory / "node"
        node.write_text(
            f'#!/bin/sh\n: > "$CHECKS_FIXTURE_MARKERS/node"\nprintf \'%s\\n\' "{node_summary}"\nexit {node_exit}\n'
        )
        node.chmod(0o700)
        inherited = os.environ.get("PYTHONPATH")
        return {
            "BUDDY_NODE": str(node),
            "CHECKS_FIXTURE_MARKERS": str(self.markers),
            "PYTHONPATH": os.pathsep.join([str(self.modules)] + ([inherited] if inherited else [])),
        }

    def run_parallel(self, names: list[str], *, jobs: int = 2, node_exit: int = 0,
                     node_summary: str = "") -> tuple[str | None, str]:
        # Each run owns a fresh private root, exactly as ``main`` creates one per run.
        self.private_root = Path(tempfile.mkdtemp(prefix="root-", dir=self.directory))
        output = io.StringIO()
        with (
            patch.dict(os.environ, self.environment(node_exit, node_summary)),
            patch.object(checks, "python_test_modules", return_value=list(names)),
            contextlib.redirect_stdout(output),
        ):
            failure = checks.run_suites_parallel(REPOSITORY, self.private_root, jobs)
        return failure, output.getvalue()


class ParallelOutcomeTests(RunnerTestCase):
    """Every way a file can fail fails the run, and the totals stay visible."""

    def passing(self) -> str:
        return self.module(
            "checksfixture_pass",
            """
            import os, unittest
            from pathlib import Path

            class Passing(unittest.TestCase):
                def test_runs_in_its_own_private_root(self):
                    root = Path(os.environ["BUDDY_STATE_DIR"]).parent
                    self.assertEqual(Path(os.environ["TMPDIR"]), root / "tmp")
                    self.assertEqual(Path(os.environ["BUDDY_RUNTIME_ROOT"]), root / "runtime")
                    (Path(os.environ["CHECKS_FIXTURE_MARKERS"]) / "root").write_text(str(root))

                @unittest.skip("fixture")
                def test_is_skipped(self):
                    pass
            """,
        )

    def test_a_passing_run_reports_its_totals_and_runs_the_node_suite(self):
        failure, output = self.run_parallel([self.passing()], node_summary="ℹ tests 7")
        self.assertIsNone(failure, output)
        self.assertIn("hey_my_buddy.cli.checks: python tests run: 2 (skipped 1) in 1 of 1 files", output)
        self.assertIn("hey_my_buddy.cli.checks: node tests run: 7", output)
        self.assertTrue((self.markers / "node").exists())
        child_root = Path((self.markers / "root").read_text())
        self.assertEqual(child_root.parent, self.private_root)

    def test_every_kind_of_failing_file_fails_the_run(self):
        names = [
            self.passing(),
            self.module(
                "checksfixture_assertion",
                """
                import unittest

                class Failing(unittest.TestCase):
                    def test_breaks(self):
                        self.assertEqual(1, 2, "fixture assertion")
                """,
            ),
            self.module("checksfixture_import", "import a_module_that_does_not_exist\n"),
            self.module("checksfixture_empty", "import unittest\n"),
            self.module(
                "checksfixture_killed",
                """
                import os, signal, unittest

                class Killed(unittest.TestCase):
                    def test_dies(self):
                        os.kill(os.getpid(), signal.SIGKILL)
                """,
            ),
        ]
        failure, output = self.run_parallel(names)
        self.assertIsNotNone(failure)
        failing = ["checksfixture_assertion", "checksfixture_empty", "checksfixture_import", "checksfixture_killed"]
        self.assertIn(f"python suite failed in 4 file(s): {', '.join(failing)}", failure)
        self.assertNotIn("checksfixture_pass", failure)
        self.assertIn("hey_my_buddy.cli.checks: python tests run: 2 (skipped 1) in 1 of 5 files", output)
        # The failing test, its message and the file it belongs to are all printed.
        self.assertIn("----- checksfixture_assertion stderr -----", output)
        self.assertIn("test_breaks (checksfixture_assertion.Failing.test_breaks)", output)
        self.assertIn("fixture assertion", output)
        self.assertIn("a_module_that_does_not_exist", output)
        self.assertIn(f"checksfixture_killed FAILED (exit {-signal.SIGKILL})", output)

    def test_a_failing_node_suite_fails_the_run(self):
        failure, output = self.run_parallel([self.passing()], node_exit=7)
        self.assertEqual(failure, "node suite exited with 7")
        self.assertIn("node suite FAILED (exit 7)", output)

    def test_a_node_suite_that_ran_nothing_fails_the_run(self):
        failure, _ = self.run_parallel([self.passing()], node_summary="ℹ tests 0")
        self.assertEqual(failure, "node suite ran no tests")
        failure, output = self.run_parallel([self.passing()])
        self.assertIsNone(failure, output)
        self.assertIn("hey_my_buddy.cli.checks: node tests run: not reported", output)

    def test_a_checkout_without_node_tests_is_refused(self):
        self.assertTrue(checks.dsh_node_tests(REPOSITORY))
        with self.assertRaises(SystemExit):
            checks.dsh_node_tests(self.directory)

    def test_a_file_that_reports_no_test_count_fails_the_run(self):
        def silent(root, private_root, directory_name, label, command, live, guard, stop):
            return checks.ChildOutcome(label, 0, "", "nothing was counted here\n", 0.0)

        with patch.object(checks, "run_suite_child", silent):
            failure, _ = self.run_parallel(["checksfixture_silent"])
        self.assertEqual(failure, "python suite reported no test count in 1 file(s): checksfixture_silent")

    def test_a_scheduled_suite_without_an_outcome_fails_the_run(self):
        def partial(root, private_root, directory_name, label, command, live, guard, stop):
            if label == "checksfixture_lost":
                return None
            return checks.ChildOutcome(label, 0, "", "Ran 1 test in 0.001s\n\nOK\n", 0.0)

        with patch.object(checks, "run_suite_child", partial):
            failure, _ = self.run_parallel(["checksfixture_kept", "checksfixture_lost"])
        self.assertEqual(failure, "1 suite(s) never ran: checksfixture_lost")

    def test_a_coloured_summary_is_still_counted(self):
        # The summary Python 3.14 prints when colour is forced.
        summary = "Ran 4 tests in 0.002s\n\n\x1b[32mOK\x1b[0m (\x1b[33mskipped=3\x1b[0m)\n"

        def coloured(root, private_root, directory_name, label, command, live, guard, stop):
            return checks.ChildOutcome(label, 0, "", summary, 0.0)

        with patch.object(checks, "run_suite_child", coloured):
            failure, output = self.run_parallel(["checksfixture_coloured"])
        self.assertIsNone(failure)
        self.assertIn("hey_my_buddy.cli.checks: python tests run: 4 (skipped 3) in 1 of 1 files", output)


class LeftoverProcessTests(RunnerTestCase):
    """A process a suite leaves behind never holds the runner or an interrupted run."""

    def reap(self, pid: int) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)

    def test_a_leftover_process_holding_the_output_does_not_hold_the_runner(self):
        script = (
            "import subprocess, sys\n"
            "leftover = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], "
            "start_new_session=True)\n"
            "print(leftover.pid, flush=True)\n"
        )
        started = time.monotonic()
        outcome = checks.run_suite_child(
            REPOSITORY, self.private_root, "p000", "leftover", [sys.executable, "-c", script],
            set(), threading.Lock(), threading.Event(),
        )
        elapsed = time.monotonic() - started
        leftover = int(outcome.stdout.strip())
        self.addCleanup(self.reap, leftover)
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        self.assertLess(elapsed, 60, "the runner waited for a process its child left behind")
        self.assertTrue(process_exists(leftover), "the fixture process must still hold the inherited pipes")

    def test_an_interrupted_run_starts_nothing_further_and_stops_its_suite_children(self):
        slow = """
            import json, os, subprocess, sys, time, unittest
            from pathlib import Path

            class Slow(unittest.TestCase):
                def test_blocks(self):
                    leftover = subprocess.Popen(
                        [sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True
                    )
                    marker = Path(os.environ["CHECKS_FIXTURE_MARKERS"]) / f"{__name__}.json"
                    marker.write_text(json.dumps({"pid": os.getpid(), "leftover": leftover.pid}))
                    time.sleep(120)
        """
        names = [self.module(f"checksfixture_slow_{letter}", slow) for letter in "abc"]
        driver = self.directory / "driver.py"
        driver.write_text(textwrap.dedent(
            """
            import sys
            from pathlib import Path
            from unittest.mock import patch

            from hey_my_buddy.cli import checks

            with patch.object(checks, "python_test_modules", return_value=sys.argv[3:]):
                checks.run_suites_parallel(Path(sys.argv[1]), Path(sys.argv[2]), 2)
            """
        ))
        environment = {**os.environ, **self.environment()}
        environment["PYTHONPATH"] = os.pathsep.join([str(REPOSITORY / "src"), environment["PYTHONPATH"]])
        log = (self.directory / "driver.log").open("w")
        self.addCleanup(log.close)
        process = subprocess.Popen(
            [sys.executable, str(driver), str(REPOSITORY), str(self.private_root), *names],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.addCleanup(lambda: process.poll() is None and (process.kill(), process.wait(timeout=30)))

        def started() -> list[dict] | None:
            found = []
            for name in names[:2]:
                try:
                    found.append(json.loads((self.markers / f"{name}.json").read_text()))
                except (OSError, ValueError):
                    return None
            return found

        children = wait_until(started, timeout=60)
        self.assertTrue(children, "the first two fixture files never started")
        for child in children:
            self.addCleanup(self.reap, child["leftover"])
            self.addCleanup(self.reap, child["pid"])
        self.assertFalse((self.markers / f"{names[2]}.json").exists(), "two workers must leave the third file queued")

        interrupted = time.monotonic()
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            self.fail("the interrupted run did not stop: " + (self.directory / "driver.log").read_text()[-2000:])
        elapsed = time.monotonic() - interrupted
        output = (self.directory / "driver.log").read_text()
        self.assertNotEqual(process.returncode, 0, output)
        self.assertIn("KeyboardInterrupt", output)
        self.assertLess(elapsed, 60, "the interrupted run waited for the processes its children left behind")
        for child in children:
            self.assertFalse(process_exists(child["pid"]), "an interrupted run left a suite child running")
            self.assertTrue(process_exists(child["leftover"]), "the fixture process must outlive its suite child")
        self.assertFalse((self.markers / f"{names[2]}.json").exists(), "a queued file started after the interrupt")
        self.assertFalse((self.markers / "node").exists(), "the queued node suite started after the interrupt")


if __name__ == "__main__":
    unittest.main()
