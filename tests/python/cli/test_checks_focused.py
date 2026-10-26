"""Focused checks share the full runner's process isolation and cleanup contract."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from hey_my_buddy.cli import checks
from hey_my_buddy import process_observation as observation
from hey_my_buddy.errors import BoardError
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

    def frontend(self):
        """Synthetic entrypoints use Python as Node, without real frontend tests."""
        console = self.root / "apps/console"
        source = console / "src"
        (source / "nested").mkdir(parents=True)
        (source / "index.html").write_bytes(b"<script src='nested/main.js'></script>\n")
        (source / "nested/main.js").write_bytes(b"console.log('source');\n")
        (source / "nested/icon.bin").write_bytes(b"\x00\xff")
        self.assets = self.root / "src/hey_my_buddy/console/assets"
        shutil.copytree(source, self.assets)
        for name, relative, arguments in (
            ("frontend-typecheck", "typescript/bin/tsc", ["--noEmit"]),
            ("frontend-vitest", "vitest/vitest.mjs", ["run", "--maxWorkers=1", "--reporter=dot"]),
            ("frontend-build", "vite/bin/vite.js", None),
        ):
            entry = console / "node_modules" / relative
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text(
                "import json, os, shutil, sys, time\nfrom pathlib import Path\n"
                f"assert Path.cwd() == Path({str(console)!r})\n"
                "root = Path(os.environ['HMB_STATE_DIR']).parent\n"
                + (f"assert sys.argv[1:] == {arguments!r}, sys.argv\n" if arguments is not None else
                   "assert sys.argv[1:] == ['build', '--outDir', str(root / 'assets')], sys.argv\n"
                   "assert not (root / 'assets').exists(), 'build must use a fresh private directory'\n"
                   "shutil.copytree(Path.cwd() / 'src', root / 'assets')\n")
                + "assert Path(os.environ['TMPDIR']) == root / 'tmp'\n"
                "assert Path(os.environ['HMB_RUNTIME_ROOT']) == root / 'runtime'\n"
                "assert Path.home().parent == root.parent\n"
                "assert 'HMB_RUNTIME' not in os.environ\n"
                "assert 'HMB_AGENT_CREDENTIAL_FD' not in os.environ\n"
                f"marker = Path({str(self.markers)!r}) / {name!r}\n"
                "assert not marker.exists(), 'frontend command ran twice'\n"
                "started = time.monotonic()\ntime.sleep(0.1)\n"
                "marker.write_text(json.dumps({'root': str(root), 'start': started, "
                "'end': time.monotonic()}), encoding='utf-8')\n"
                + ("print('Test Files  1 passed (1)\\n     Tests  2 passed (2)')\n"
                   if name == "frontend-vitest" else ""),
                encoding="utf-8", newline="\n",
            )
        return console

    def invoke(self, arguments, *, process_table=(), node=sys.executable):
        observer = (patch.object(observation.psutil, "pids", side_effect=observation.psutil.AccessDenied())
                    if process_table is None else
                    processes(*(process(pid, argv) for pid, argv in process_table)))
        output = io.StringIO()
        errors = io.StringIO()
        with (
            patch.object(checks, "__file__", str(self.root / "src/hey_my_buddy/cli/checks.py")),
            patch.dict(os.environ, {"HMB_CHECKS_TMPDIR": str(self.temporary),
                                    "HMB_RUNTIME": "/fixture/pinned-runtime",
                                    "HMB_AGENT_CREDENTIAL_FD": "17", "HMB_NODE": node}),
            observer,
            patch.object(checks, "_windows_owned_processes",
                         return_value=([], ["fixture process observation incomplete"], []))
            if os.name == "nt" and process_table is None else contextlib.nullcontext(),
            patch.object(checks, "_open_file_pids", return_value=(set(), None, True, [])),
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

    def test_no_paths_runs_frontend_once_and_the_original_full_file_set(self):
        self.frontend()
        before = {path.relative_to(self.assets): path.read_bytes()
                  for path in self.assets.rglob("*") if path.is_file()}
        code, output, errors = self.invoke(["--jobs", "1"])
        self.assertEqual(code, 0, errors + output)
        self.assertEqual({path.name for path in self.markers.iterdir()},
                         set(checks.python_test_modules(self.root)) |
                         {"frontend-typecheck", "frontend-vitest", "frontend-build"})
        self.assertIn("apps/console ok", output)
        self.assertIn("Packaged console assets match the fresh build (3 files)", output)
        self.assertEqual({path.relative_to(self.assets): path.read_bytes()
                          for path in self.assets.rglob("*") if path.is_file()}, before)
        self.assertIn("Tests  2 passed (2)", output)
        self.assertIn("python tests run: 3 (skipped 0) in 3 of 3 files", output)
        self.assertIn("tests passed; private root removed; exit 0", output.splitlines()[-1])
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_unrebuilt_source_fails_even_with_matching_asset_size_and_mtime(self):
        console = self.frontend()
        source = console / "src/nested/main.js"
        original = source.stat()
        source.write_bytes(b"console.log('change');\n")
        os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
        code, output, errors = self.invoke(["--jobs", "1"])
        self.assertEqual(code, 1, errors + output)
        self.assertIn("Files with different content:\n  nested/main.js\n", output)
        self.assertIn("Run `npm --prefix apps/console run build`", output)
        self.assertIn("frontend suite failed: apps/console", output.splitlines()[-1])
        self.assertEqual((self.assets / "nested/main.js").read_bytes(), b"console.log('source');\n")
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_extra_packaged_asset_fails_and_names_the_file(self):
        self.frontend()
        extra = self.assets / "nested/.stale.bin"
        extra.write_bytes(b"obsolete")
        code, output, errors = self.invoke(["--jobs", "1"])
        self.assertEqual(code, 1, errors + output)
        self.assertIn("Extra packaged files:\n  nested/.stale.bin\n", output)
        self.assertEqual(extra.read_bytes(), b"obsolete")
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_missing_packaged_asset_fails_and_names_the_file(self):
        self.frontend()
        missing = self.assets / "nested/icon.bin"
        missing.unlink()
        code, output, errors = self.invoke(["--jobs", "1"])
        self.assertEqual(code, 1, errors + output)
        self.assertIn("Missing packaged files:\n  nested/icon.bin\n", output)
        self.assertFalse(missing.exists())
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_build_failure_keeps_its_exit_code_without_comparing_partial_output(self):
        console = self.frontend()
        (console / "node_modules/vite/bin/vite.js").write_text(
            "print('fixture build failed')\nraise SystemExit(7)\n", encoding="utf-8", newline="\n")
        code, output, errors = self.invoke(["--jobs", "1"])
        self.assertEqual(code, 1, errors + output)
        self.assertIn("apps/console FAILED (exit 7)", output)
        self.assertIn("fixture build failed", output)
        self.assertNotIn("packaged files:", output)
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_missing_node_or_frontend_dependency_fails_with_install_instruction(self):
        console = self.frontend()
        for missing in ("node", "typescript/bin/tsc", "vitest/vitest.mjs", "vite/bin/vite.js"):
            with self.subTest(missing=missing):
                for marker in self.markers.iterdir():
                    marker.unlink()
                node = str(self.root / "missing-node") if missing == "node" else sys.executable
                entry = console / "node_modules" / missing
                if missing != "node":
                    entry.rename(entry.with_suffix(".missing"))
                try:
                    code, output, errors = self.invoke(["--jobs", "1"], node=node)
                finally:
                    if missing != "node":
                        entry.with_suffix(".missing").rename(entry)
                self.assertEqual(code, 1, errors + output)
                self.assertIn("apps/console FAILED (exit 1)", output)
                self.assertIn("run `npm --prefix apps/console ci` first", output)
                self.assertIn("frontend suite failed: apps/console", output.splitlines()[-1])
                self.assertIn("private root removed; exit 1", output.splitlines()[-1])
                self.assertEqual({path.name for path in self.markers.iterdir()},
                                 set(checks.python_test_modules(self.root)))
                self.assertEqual(list(self.temporary.iterdir()), [])

    def test_frontend_uses_one_slot_within_the_jobs_limit_and_is_first_when_serial(self):
        self.frontend()
        for module in checks.python_test_modules(self.root):
            self.top.joinpath(*module.split(".")).with_suffix(".py").write_text(
                "import json, time, unittest\nfrom pathlib import Path\n"
                "class Case(unittest.TestCase):\n"
                "    def test_runs(self):\n"
                "        started = time.monotonic()\n        time.sleep(0.1)\n"
                f"        marker = Path({str(self.markers)!r}) / __name__\n"
                "        marker.write_text(json.dumps({'start': started, 'end': time.monotonic()}), "
                "encoding='utf-8')\n", encoding="utf-8", newline="\n",
            )
        for jobs in (1, 2):
            with self.subTest(jobs=jobs):
                for marker in self.markers.iterdir():
                    marker.unlink()
                code, output, errors = self.invoke(["--jobs", str(jobs)])
                self.assertEqual(code, 0, errors + output)
                intervals = {path.name: json.loads(path.read_text(encoding="utf-8"))
                             for path in self.markers.iterdir()}
                typecheck, vitest, build = (intervals.pop(f"frontend-{name}")
                                           for name in ("typecheck", "vitest", "build"))
                self.assertEqual(typecheck["root"], vitest["root"])
                self.assertEqual(typecheck["root"], build["root"])
                self.assertLess(typecheck["end"], vitest["start"])
                self.assertLess(vitest["end"], build["start"])
                frontend = {"start": typecheck["start"], "end": build["end"]}
                active = peak = 0
                for _, delta in sorted((interval[key], delta)
                                       for interval in [frontend, *intervals.values()]
                                       for key, delta in (("start", 1), ("end", -1))):
                    active += delta
                    peak = max(peak, active)
                self.assertLessEqual(peak, jobs, "frontend exceeded the shared worker count")
                if jobs == 1:
                    previous = frontend
                    for module in checks.python_test_modules(self.root):
                        self.assertLess(previous["end"], intervals[module]["start"])
                        previous = intervals[module]
                self.assertEqual(list(self.temporary.iterdir()), [])

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
        if os.name == "nt":
            self.assertEqual(evidence["observation"]["problems"], ["fixture process observation incomplete"])
        else:
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

    def test_removal_failure_preserves_root_and_exits_three(self):
        error = PermissionError("fixture deletion denied")
        with patch.object(checks, "remove_private_test_root", side_effect=error):
            code, output, errors = self.invoke(["tests/python/test_first.py"])
        self.assertEqual(code, 3, errors + output)
        retained, = self.temporary.iterdir()
        evidence = json.loads((retained / checks.EVIDENCE_FILE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(evidence["cleanupError"], "PermissionError: fixture deletion denied")
        self.assertIn(f"tests passed; teardown incomplete; preserved {retained}; exit 3",
                      output.splitlines()[-1])


class WindowsSweepTests(unittest.TestCase):
    def test_windows_refuses_sweep_without_changing_roots_or_evidence(self):
        with tempfile.TemporaryDirectory(prefix="checks-sweep-refused-") as raw:
            directory = Path(raw)
            root = checks.create_private_root(directory=directory)
            original = b'{"original":"shutdown unknown"}\n'
            evidence = root / checks.EVIDENCE_FILE_NAME
            evidence.write_bytes(original)
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            errors = io.StringIO()
            with (patch.object(checks, "os", wraps=os) as windows,
                  patch.object(checks, "sweep_private_roots") as sweep,
                  patch.dict(os.environ, {"HMB_CHECKS_TMPDIR": str(directory)}),
                  contextlib.redirect_stderr(errors)):
                windows.name = "nt"
                with self.assertRaises(SystemExit) as result:
                    checks.main(["--sweep"])
            self.assertEqual(result.exception.code, 2)
            self.assertIn("without the original process and Job handles", errors.getvalue())
            self.assertIn("inspect retained evidence", errors.getvalue())
            self.assertIn("remove roots manually", errors.getvalue())
            sweep.assert_not_called()
            self.assertTrue(root.exists())
            after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(after, before)
            self.assertEqual(evidence.read_bytes(), original)
            checks._windows_suite_children.pop(root, None)


@unittest.skipIf(os.name == "nt", "POSIX sweep; Windows rejects --sweep before inspecting roots")
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
        observer = (patch.object(observation.psutil, "pids", side_effect=observation.psutil.AccessDenied())
                    if table is None else processes(*(process(pid, argv) for pid, argv in table)))
        output = io.StringIO()
        with (observer,
              patch.object(checks, "_open_file_pids", return_value=(set(), None, True, [])),
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

    def test_sweep_reports_rejected_private_boundary_and_preserves_root(self):
        root = self.abandoned()
        error = BoardError("PRIVATE_PATH_UNSAFE", "State root has a foreign owner")
        with (patch.object(checks.state_root, "check_state_root", side_effect=error),
              patch.object(checks, "_safe_teardown") as teardown,
              patch.object(checks, "_write_evidence") as write_evidence):
            code, output = self.sweep([])
        self.assertEqual(code, 3, output)
        self.assertTrue(root.exists())
        self.assertEqual(sorted(path.name for path in root.iterdir()), [checks.CREATOR_FILE_NAME, "tmp"])
        teardown.assert_not_called()
        write_evidence.assert_not_called()
        self.assertIn(f"retained {root}: outside this account's private boundary", output)
        self.assertIn("sweep deleted 0, skipped 0, retained 1", output)

    @unittest.skipIf(os.name == "nt", "POSIX account ownership")
    def test_sweep_does_not_touch_another_users_directory(self):
        root = self.abandoned()
        original = Path.stat
        def foreign(path, *args, **kwargs):
            metadata = original(path, *args, **kwargs)
            if path == root:
                fields = list(metadata)
                fields[4] = os.getuid() + 1
                return os.stat_result(fields)
            return metadata
        with patch.object(Path, "stat", foreign):
            code, output = self.sweep([])
        self.assertEqual(code, 3, output)
        self.assertTrue(root.exists())
        self.assertIn("sweep deleted 0, skipped 0, retained 1", output)

    @unittest.skipIf(os.name == "nt", "POSIX owner-private mode")
    def test_sweep_rejects_shared_permissions_without_repair(self):
        root = self.abandoned()
        root.chmod(0o755)
        code, output = self.sweep([])
        self.assertEqual(code, 3, output)
        self.assertEqual(root.stat().st_mode & 0o777, 0o755)
        self.assertTrue((root / checks.CREATOR_FILE_NAME).is_file())
        self.assertIn("sweep deleted 0, skipped 0, retained 1", output)

    def test_default_sweep_location_matches_root_creation_location(self):
        environment = {key: value for key, value in os.environ.items() if key != "HMB_CHECKS_TMPDIR"}
        with patch.dict(os.environ, environment, clear=True):
            with patch.object(checks.sys, "platform", "darwin"):
                self.assertEqual(checks.private_root_directory(), Path("/tmp"))
            with patch.object(checks.sys, "platform", "linux"):
                self.assertEqual(checks.private_root_directory(), Path(tempfile.gettempdir()))


if __name__ == "__main__":
    unittest.main()
