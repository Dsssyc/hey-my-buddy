"""Installation keeps its existing policy for linked ancestor directories."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.install import launcher, runtime, skill_install


class InstallationPublicationPathTests(unittest.TestCase):
    def test_launcher_metadata_and_skill_hint_allow_a_linked_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real-home"
            real.mkdir()
            linked = root / "linked-home"
            linked.symlink_to(real, target_is_directory=True)
            state, skill = linked / ".local/state", linked / ".agents/skills/hey-my-buddy"
            state.mkdir(parents=True)
            skill.mkdir(parents=True)
            target = linked / ".local/runtimes" / ("a" * 32)

            launcher.write_private(state / "active-runtime.json", {"runtimeDir": str(target)})
            launcher.write_private(state / "active-runtime.json", {"runtimeDir": "replacement"})
            skill_install.write_runtime_hint(skill, target)

            self.assertEqual((real / ".local/state/active-runtime.json").read_bytes(),
                             b'{"runtimeDir": "replacement"}')
            self.assertEqual((real / ".agents/skills/hey-my-buddy/.runtime-python").read_bytes(),
                             (str(runtime.runtime_python(target)) + "\n").encode("utf-8"))

    def test_runtime_manifest_and_ready_allow_a_linked_runtime_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real-runtime-root"
            real.mkdir()
            linked = root / "linked-runtime-root"
            linked.symlink_to(real, target_is_directory=True)
            target = linked / ("b" * 32)
            source = root / "source"
            source.mkdir()

            def install(*args, **kwargs):
                interpreter = runtime.runtime_python(target)
                interpreter.parent.mkdir(parents=True)
                interpreter.write_bytes(b"fixture-python")
                return SimpleNamespace(returncode=0)

            with (mock.patch.object(runtime, "_copy_assets"),
                  mock.patch.object(runtime, "missing_resources", return_value=[]),
                  mock.patch.object(runtime, "manifest", return_value={"manifestSha256": "digest"}),
                  mock.patch.object(runtime.subprocess, "run", side_effect=install),
                  mock.patch.object(runtime, "_rewrite_environment_paths"),
                  mock.patch.object(runtime, "_source_commit", return_value=None),
                  mock.patch.object(runtime, "resource_paths", return_value={}),
                  mock.patch.object(runtime, "read_ready", return_value={})):
                result = runtime._materialize_locked(source, target, "fixture-uv", 10, None)

            self.assertTrue(result["installed"])
            self.assertEqual((real / target.name / "manifest.json").read_bytes(),
                             b'{\n  "manifestSha256": "digest"\n}\n')
            self.assertEqual((target / runtime.READY_FILE).read_bytes(),
                             (real / target.name / runtime.READY_FILE).read_bytes())
            self.assertEqual(list(linked.glob(".*.build-*")), [])
