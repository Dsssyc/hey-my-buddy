"""Private installed daemon startup keeps the selected runtime environment."""
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv
from support import BoardTestCase
from hey_my_buddy.install import launcher, runtime, upgrade


ROOT = Path(__file__).resolve().parents[3]
DAEMON_FIXTURE = ROOT / "tests/python/blackboard/service/fixtures/daemon_with_catalog.py"


class HostCutoverTests(BoardTestCase):
    def private_runtime(self, generation, *, record_environment=False):
        root = self.directory / "runtime"
        source = root / ("fixture-" + generation)
        source.mkdir(parents=True)
        runtime._copy_assets(ROOT, source)
        fixture_text = DAEMON_FIXTURE.read_text()
        bootstrap = fixture_text
        if record_environment:
            entry = "    raise SystemExit(main())"
            self.assertEqual(bootstrap.count(entry), 1, "Offline daemon fixture entry changed")
            bootstrap = bootstrap.replace(
                entry,
                "    (Path(os.environ['HMB_STATE_DIR']) / 'daemon-environment.json').write_text(\n"
                "        json.dumps({'PATH': os.environ.get('PATH'), 'VIRTUAL_ENV': os.environ.get('VIRTUAL_ENV')}))\n"
                + entry,
            )
        (source / "src/hey_my_buddy/_upgrade_fixture_daemon.py").write_text(
            bootstrap + "\n# Fixture generation: " + generation + "\n"
        )
        target = root / runtime.content_id(source)
        source.rename(target)
        environment = target / "venv"
        venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
        # EnvBuilder records its creation command, including the fixture's own
        # runtime path. Installed uv environments have no such command field.
        configuration = environment / "pyvenv.cfg"
        configuration.write_text("\n".join(line for line in configuration.read_text().splitlines()
                                           if not line.startswith("command =")) + "\n")
        site = environment / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        for name in ("c_two", "fastdb4py", "numpy", "portalocker", "pydantic", "pydantic_core", "zstandard",
                     "typing_extensions", "typing_inspection", "annotated_types"):
            module = importlib.import_module(name)
            path = Path(module.__file__)
            if path.name == "__init__.py":
                shutil.copytree(path.parent, site / name, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(path, site / path.name)
            distribution = metadata.distribution(name.replace("_", "-"))
            metadata_dir = next(file.parts[0] for file in distribution.files if file.parts[0].endswith(".dist-info"))
            shutil.copytree(distribution.locate_file(metadata_dir), site / metadata_dir)
        (site / "hey-my-buddy-fixture.pth").write_text(str(target / "src") + "\n")
        ready = {
            "format": runtime.RUNTIME_FORMAT, "state": "READY", "contentId": target.name,
            "source": str(target), "projectRoot": str(target), "environment": str(environment),
            "python": str(runtime.runtime_python(target)), "runtimeRoot": str(root),
            "resources": {name: str(path) for name, path in runtime.resource_paths(target).items()},
            "sourceCommit": None,
        }
        (target / "READY.json").write_text(json.dumps(ready))
        self.assertTrue(runtime.is_ready(target))
        return target

    def test_upgrade_started_service_excludes_installer_venv_bin_from_path(self):
        installer = self.private_runtime("installer")
        target = self.private_runtime("service", record_environment=True)
        self.catalog_fixture()
        installer_bin = str(runtime.runtime_python(installer).parent)
        markers = [str(self.directory / name) for name in ("before", "middle", "after")]
        original_entries = os.environ.get("PATH", "").split(os.pathsep)
        inherited_entries = [markers[0], installer_bin, markers[1], *original_entries,
                             installer_bin, markers[2]]
        environment = self.child_environment({"HMB_RUNTIME_ROOT": str(installer.parent),
                                              "PATH": os.pathsep.join(inherited_entries)})
        # The package launcher has execed into the installer interpreter by
        # this point, so VIRTUAL_ENV is absent even though sys.prefix is a venv.
        for key in ("PYTHONPATH", "HMB_DEV_SOURCE", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
            environment.pop(key, None)
        coordinator = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "from unittest import mock\n"
            "from hey_my_buddy.install import launcher, upgrade\n"
            "state, target = map(Path, sys.argv[1:])\n"
            "assert sys.prefix != sys.base_prefix\n"
            "original = launcher.entry_modules\n"
            "fixture = lambda path: {**original(path), 'daemon': 'hey_my_buddy._upgrade_fixture_daemon'}\n"
            "with mock.patch.object(launcher, 'entry_modules', side_effect=fixture):\n"
            "    print(json.dumps(upgrade.start(state, target)))"
        )
        result = subprocess.run([str(runtime.runtime_python(installer)), "-c", coordinator,
                                 str(self.directory), str(target)], cwd=installer, env=environment,
                                capture_output=True, text=True, timeout=65)
        log = self.directory / "upgrade-start.log"
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout +
                         (log.read_text()[-5000:] if log.exists() else ""))
        self.assertTrue(json.loads(result.stdout)["runtimeStable"])
        recorded = json.loads((self.directory / "daemon-environment.json").read_text())
        self.assertIsNone(recorded["VIRTUAL_ENV"])
        service_entries = recorded["PATH"].split(os.pathsep)
        self.assertNotIn(installer_bin, service_entries)
        self.assertEqual(service_entries,
                         [entry for entry in inherited_entries if entry != installer_bin])
