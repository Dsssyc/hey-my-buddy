"""Focused checks share the full runner's process isolation and cleanup contract."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hey_my_buddy.cli import checks
from hey_my_buddy.blackboard.tasks import storage
from process_observation_fakes import process, processes


class FocusedChecksTests(unittest.TestCase):
    def setUp(self):
        self.container = tempfile.TemporaryDirectory(prefix="checks-focused-")
        self.addCleanup(self.container.cleanup)
        self.root = Path(self.container.name).resolve()
        self.top = self.root / "tests/python"
        self.temporary = self.root / "temporary"
        self.temporary.mkdir()
        self.markers = self.root / "markers"
        self.markers.mkdir()
        for relative in ("test_first.py", "nested/test_second.py", "nested/deeper/test_third.py"):
            file = self.top / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            for parent in file.parents:
                if parent == self.top:
                    break
                (parent / "__init__.py").touch()
            file.write_text(
                "import os, unittest\nfrom pathlib import Path\n"
                "class Case(unittest.TestCase):\n"
                "    def test_private_environment(self):\n"
                "        state = Path(os.environ['HMB_STATE_DIR'])\n"
                "        self.assertEqual(Path(os.environ['TMPDIR']), state.parent / 'tmp')\n"
                "        self.assertEqual(Path(os.environ['HMB_RUNTIME_ROOT']), state.parent / 'runtime')\n"
                "        self.assertNotIn('HMB_RUNTIME', os.environ)\n"
                "        self.assertNotIn('HMB_AGENT_CREDENTIAL_FD', os.environ)\n"
                "        self.assertEqual(Path.home().parent, state.parent.parent)\n"
                f"        marker = Path({str(self.markers)!r}) / __name__\n"
                "        self.assertFalse(marker.exists(), 'file ran twice')\n"
                "        marker.write_text(str(state.parent), encoding='utf-8', newline='\\n')\n", encoding="utf-8", newline="\n"
            )
        (self.top / "empty").mkdir()
        (self.top / "helper.py").write_text("", encoding="utf-8", newline="\n")
        unreachable = self.top / "plain/test_unreachable.py"
        unreachable.parent.mkdir()
        unreachable.write_text("", encoding="utf-8", newline="\n")

    def invoke(self, arguments, *, process_table=()):
        observer = (patch.object(storage.psutil, "pids", side_effect=storage.psutil.AccessDenied())
                    if process_table is None else
                    processes(*(process(pid, argv) for pid, argv in process_table)))
        output = io.StringIO()
        errors = io.StringIO()
        with (
            patch.object(checks, "__file__", str(self.root / "src/hey_my_buddy/cli/checks.py")),
            patch.dict(os.environ, {"HMB_CHECKS_TMPDIR": str(self.temporary),
                                    "HMB_RUNTIME": "/fixture/pinned-runtime",
                                    "HMB_AGENT_CREDENTIAL_FD": "17"}),
            observer,
            patch.object(checks, "_open_file_pids", return_value=(set(), None, True)),
            contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors),
        ):
            try:
                checks.main(arguments)
            except SystemExit as error:
                code = error.code
            else:
                code = 0
        return code, output.getvalue(), errors.getvalue()

    def test_selects_files_directories_mixed_and_duplicates(self):
        cases = (
            (["tests/python/test_first.py"], ["test_first"]),
            ([str(self.top / "test_first.py")], ["test_first"]),
            (["tests/python/nested"], ["nested.deeper.test_third", "nested.test_second"]),
            (["tests/python/test_first.py", "tests/python/nested"],
             ["nested.deeper.test_third", "nested.test_second", "test_first"]),
            (["tests/python/test_first.py", str(self.top / "test_first.py"), "tests/python"],
             ["nested.deeper.test_third", "nested.test_second", "test_first"]),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(checks.selected_test_modules(self.root, arguments), expected)
        self.assertEqual(checks.selected_test_modules(self.root, []),
                         checks.python_test_modules(self.root))

    def test_all_usage_errors_precede_private_root_creation(self):
        for arguments in (
            ["elsewhere/test_bad.py"], ["tests/python/test_missing.py"],
            ["tests/python/empty"], ["tests/python/helper.py"],
            ["tests/python/plain"], ["nested.test_second"],
            ["--sweep", "tests/python"], ["--sweep", "--jobs", "1"],
            ["--jobs"], ["--jobs=0"], ["--jobs=many"], ["--unknown"], ["--swe"],
        ):
            with self.subTest(arguments=arguments), patch.object(checks, "create_private_root") as create:
                code, _, errors = self.invoke(arguments)
                self.assertEqual(code, 2, errors)
                create.assert_not_called()
                if not arguments[0].startswith("--"):
                    self.assertIn(arguments[0], errors)
                self.assertEqual(list(self.temporary.iterdir()), [])

    def test_focused_serial_and_parallel_runs_start_each_selected_file_once(self):
        for jobs in (1, 2):
            for paths, expected in (
                (["tests/python/test_first.py"], {"test_first"}),
                (["tests/python/nested"], {"nested.test_second", "nested.deeper.test_third"}),
                (["tests/python/test_first.py", "tests/python/nested", str(self.top / "test_first.py")],
                 {"test_first", "nested.test_second", "nested.deeper.test_third"}),
            ):
                with self.subTest(jobs=jobs, paths=paths):
                    for marker in self.markers.iterdir():
                        marker.unlink()
                    code, output, errors = self.invoke(["--jobs", str(jobs), *paths])
                    self.assertEqual(code, 0, errors + output)
                    self.assertEqual({path.name for path in self.markers.iterdir()}, expected)
                    child_roots = {path.read_text(encoding="utf-8") for path in self.markers.iterdir()}
                    self.assertEqual(len(child_roots), len(expected))
                    self.assertEqual(len({Path(path).parent for path in child_roots}), 1)
                    self.assertIn(f"python tests run: {len(expected)} (skipped 0) "
                                  f"in {len(expected)} of {len(expected)} files", output)
                    self.assertIn("tests passed; private root removed; exit 0", output.splitlines()[-1])
                    self.assertEqual(list(self.temporary.iterdir()), [])

    def test_no_paths_runs_the_original_full_file_set(self):
        code, output, errors = self.invoke(["--jobs", "1"])
        self.assertEqual(code, 0, errors + output)
        self.assertEqual({path.name for path in self.markers.iterdir()},
                         set(checks.python_test_modules(self.root)))

    def test_failed_test_has_exit_one_with_either_cleanup_result(self):
        (self.top / "test_failure.py").write_text(
            "import unittest\nclass Case(unittest.TestCase):\n"
            "    def test_fails(self):\n        self.fail('fixture failure')\n", encoding="utf-8", newline="\n"
        )
        for table in ((), None):
            with self.subTest(observable=table is not None):
                code, output, errors = self.invoke(
                    ["--jobs", "1", "tests/python/test_failure.py"], process_table=table)
                self.assertEqual(code, 1, errors + output)
                self.assertIn("tests failed:", output.splitlines()[-1])
                self.assertIn("exit 1", output.splitlines()[-1])
                if table is None:
                    retained, = self.temporary.iterdir()
                    self.assertIn(str(retained), output.splitlines()[-1])
                    self.assertTrue((retained / checks.EVIDENCE_FILE_NAME).is_file())

    def test_passing_tests_with_unknown_teardown_have_exit_three_and_evidence(self):
        code, output, errors = self.invoke(
            ["--jobs=1", "tests/python/test_first.py"], process_table=None)
        self.assertEqual(code, 3, errors + output)
        retained, = self.temporary.iterdir()
        self.assertEqual(int((retained / checks.CREATOR_FILE_NAME).read_text(encoding="utf-8")), os.getpid())
        evidence = json.loads((retained / checks.EVIDENCE_FILE_NAME).read_text(encoding="utf-8"))
        self.assertIn("psutil process enumeration incomplete", evidence["observation"]["problems"][0])
        self.assertIn(f"tests passed; teardown incomplete; preserved {retained}; exit 3",
                      output.splitlines()[-1])

    def test_teardown_exception_preserves_written_evidence_and_test_result(self):
        with patch.object(checks, "teardown_private_root", side_effect=RuntimeError("fixture teardown")):
            code, output, errors = self.invoke(["--jobs=1", "tests/python/test_first.py"])
        self.assertEqual(code, 3, errors + output)
        retained, = self.temporary.iterdir()
        evidence = json.loads((retained / checks.EVIDENCE_FILE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(evidence["teardownError"], "RuntimeError: fixture teardown")
        self.assertIn(str(retained), output.splitlines()[-1])


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.container = tempfile.TemporaryDirectory(prefix="checks-sweep-")
        self.addCleanup(self.container.cleanup)
        self.directory = Path(self.container.name).resolve()

    def abandoned(self):
        root = checks.create_private_root(directory=self.directory)
        (root / checks.CREATOR_FILE_NAME).write_text("999999999\n", encoding="utf-8", newline="\n")
        return root

    def sweep(self, table):
        observer = (patch.object(storage.psutil, "pids", side_effect=storage.psutil.AccessDenied())
                    if table is None else processes(*(process(pid, argv) for pid, argv in table)))
        output = io.StringIO()
        with (observer,
              patch.object(checks, "_open_file_pids", return_value=(set(), None, True)),
              patch.dict(os.environ, {"HMB_CHECKS_TMPDIR": str(self.directory)}),
              contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO())):
            with self.assertRaises(SystemExit) as result:
                checks.main(["--sweep"])
        return result.exception.code, output.getvalue()

    def test_sweep_skips_active_deletes_clean_and_unrecorded_and_retains_residue(self):
        active = checks.create_private_root(directory=self.directory)
        clean = self.abandoned()
        unrecorded = self.abandoned()
        (unrecorded / checks.CREATOR_FILE_NAME).unlink()
        dirty = self.abandoned()
        outsider = self.directory / "other-root"
        outsider.mkdir()
        (outsider / "keep").write_text("untouched", encoding="utf-8", newline="\n")
        symlink = self.directory / "hey-my-buddy-checks-link"
        symlink.symlink_to(outsider, target_is_directory=True)
        lock = dirty / "control-daemon.lock"
        lock.write_text("", encoding="utf-8", newline="\n")
        # The drain keeps its existing behavior; a surviving owner is still residue.
        with (patch.object(checks, "request_cooperative_stop", return_value=[]),
              patch.object(checks, "lock_is_held", side_effect=lambda path: path == lock)):
            code, output = self.sweep([(os.getpid(), ["checks", "creator"])])
        self.assertEqual(code, 3, output)
        self.assertTrue(active.exists())
        self.assertFalse(clean.exists())
        self.assertFalse(unrecorded.exists())
        self.assertTrue((dirty / checks.EVIDENCE_FILE_NAME).is_file())
        self.assertIn(f"skipped {active}: creator {os.getpid()} is alive", output)
        self.assertIn(f"retained {dirty}: live process or held lifetime lock", output)
        self.assertEqual(output.splitlines()[-1],
                         "hey_my_buddy.cli.checks: sweep deleted 2, skipped 1, retained 1")
        self.assertEqual((outsider / "keep").read_text(encoding="utf-8"), "untouched")
        self.assertTrue(symlink.is_symlink())
        self.assertNotIn(str(outsider), output)

    def test_sweep_returns_zero_without_retained_roots(self):
        clean = self.abandoned()
        code, output = self.sweep([])
        self.assertEqual(code, 0, output)
        self.assertFalse(clean.exists())
        self.assertIn("sweep deleted 1, skipped 0, retained 0", output)
        code, output = self.sweep([])
        self.assertEqual(code, 0)
        self.assertIn("sweep deleted 0, skipped 0, retained 0", output)

    def test_unknown_creator_observation_preserves_root_and_evidence(self):
        root = self.abandoned()
        code, output = self.sweep(None)
        self.assertEqual(code, 3, output)
        report = json.loads((root / checks.EVIDENCE_FILE_NAME).read_text(encoding="utf-8"))
        self.assertIn("creator status unknown", report["observation"]["problems"][0])
        self.assertIn("retained", output)

    def test_unrecorded_root_still_requires_observable_teardown(self):
        root = self.abandoned()
        (root / checks.CREATOR_FILE_NAME).unlink()
        code, output = self.sweep(None)
        self.assertEqual(code, 3, output)
        report = json.loads((root / checks.EVIDENCE_FILE_NAME).read_text(encoding="utf-8"))
        self.assertIn("psutil process enumeration incomplete", report["observation"]["problems"][0])

    def test_unreadable_creator_record_is_retained(self):
        root = self.abandoned()
        (root / checks.CREATOR_FILE_NAME).write_text("not a pid", encoding="utf-8", newline="\n")
        code, output = self.sweep([])
        self.assertEqual(code, 3, output)
        self.assertIn("creator record unreadable", output)
        self.assertTrue((root / checks.EVIDENCE_FILE_NAME).is_file())

    def test_sweep_does_not_touch_another_users_directory(self):
        root = self.abandoned()
        original = Path.lstat
        def foreign(path):
            metadata = original(path)
            if path == root:
                fields = list(metadata)
                fields[4] = os.getuid() + 1
                return os.stat_result(fields)
            return metadata
        with patch.object(Path, "lstat", foreign):
            code, output = self.sweep([])
        self.assertEqual(code, 0, output)
        self.assertTrue(root.exists())
        self.assertIn("sweep deleted 0, skipped 0, retained 0", output)

    def test_default_sweep_location_matches_root_creation_location(self):
        environment = {key: value for key, value in os.environ.items() if key != "HMB_CHECKS_TMPDIR"}
        with patch.dict(os.environ, environment, clear=True):
            with patch.object(checks.sys, "platform", "darwin"):
                self.assertEqual(checks.private_root_directory(), Path("/tmp"))
            with patch.object(checks.sys, "platform", "linux"):
                self.assertEqual(checks.private_root_directory(), Path(tempfile.gettempdir()))


if __name__ == "__main__":
    unittest.main()
