"""Installation keeps its existing policy for linked ancestor directories."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.install import launcher, runtime, skill_install
from hey_my_buddy.errors import BoardError


class InstallationPublicationPathTests(unittest.TestCase):
    def test_public_install_lock_allows_linked_ancestors_but_rejects_a_link_leaf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / 'real'
            real.mkdir()
            linked = root / 'linked'
            linked.symlink_to(real, target_is_directory=True)
            lock = linked / runtime.INSTALL_LOCK
            with runtime.installation_lock(lock):
                self.assertTrue((real / runtime.INSTALL_LOCK).is_file())
            lock.unlink()
            target = root / 'foreign-lock'
            target.write_bytes(b'')
            lock.symlink_to(target)
            with self.assertRaises((OSError, BoardError)):
                with runtime.installation_lock(lock):
                    self.fail('a linked lock leaf was accepted')

    def test_private_upgrade_fence_rejects_linked_ancestors(self):
        from hey_my_buddy.install import upgrade
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'state'
            state.mkdir()
            storage = state / 'storage'
            storage.mkdir()
            foreign = root / 'foreign-lock'
            foreign.write_bytes(b'')
            (storage / '.lock').symlink_to(foreign)
            with mock.patch.object(runtime, 'materialize') as materialize:
                with self.assertRaises((OSError, BoardError)):
                    with upgrade._prepare_runtime(state, skill_source=None):
                        self.fail('a linked private state fence was accepted')
                materialize.assert_not_called()

    def test_skill_hint_allows_a_linked_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real-home"
            real.mkdir()
            linked = root / "linked-home"
            linked.symlink_to(real, target_is_directory=True)
            skill = linked / ".agents/skills/hey-my-buddy"
            skill.mkdir(parents=True)
            target = linked / ".local/runtimes" / ("a" * 32)

            skill_install.write_runtime_hint(skill, target)

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
                  mock.patch.object(runtime, "load_manifest", return_value={}),
                  mock.patch.object(runtime, "runtime_dir", return_value=target),
                  mock.patch.object(runtime, "missing_resources", return_value=[]),
                  mock.patch.object(runtime, "manifest", return_value={"manifestSha256": "digest"}),
                  mock.patch.object(runtime.subprocess, "run", side_effect=install),
                  mock.patch("hey_my_buddy.install.skill_package.source_commit", return_value=None),
                  mock.patch.object(runtime, "resource_paths", return_value={})):
                result = runtime.materialize(source, destination=linked, uv_bin="fixture-uv")

            self.assertTrue(result["installed"])
            self.assertEqual((real / target.name / "manifest.json").read_bytes(),
                             b'{\n  "manifestSha256": "digest"\n}\n')
            self.assertEqual((target / runtime.READY_FILE).read_bytes(),
                             (real / target.name / runtime.READY_FILE).read_bytes())
            self.assertEqual(runtime.read_ready(target)["state"], "READY")
            self.assertTrue(runtime.is_ready(target))
            self.assertEqual(list(linked.glob(".*.build-*")), [])
