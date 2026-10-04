"""Launch-wrapper tests: the private-home contract is enforced before any spawn.

Every rejection case uses a fake executable that would write a marker if it ran;
a rejection must leave no marker. The legal controls prove the same argv runs
under the native child environment with this run's private ``DSH_HOME`` and an
explicit private ``HOME``. Post-spawn bookkeeping failures must never lose
ownership of the child.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from importlib import import_module

from hey_my_buddy.buddy.harnesses.dsh.acp.launch import (LaunchOwnershipError, LaunchRejected,
                                                        child_environment, default_dsh_home,
                                                        launch, real_user_home)

#: The submodule object itself: the package re-exports the ``launch`` function
#: under the same name, so attribute access would resolve to the function.
launch_module = import_module("hey_my_buddy.buddy.harnesses.dsh.acp.launch")

from hey_my_buddy.buddy.harnesses.discovery import native_environment

from .support import AcpTestCase


def marker_executable(marker: Path) -> list[str]:
    return ["/bin/sh", "-c", f"echo executed >> '{marker}'"]


class LaunchRejectionTest(AcpTestCase):
    def launch_expecting_rejection(self, **kwargs) -> str:
        """Launch the marker executable with injected arguments; return the message."""
        marker = self.root / "marker.txt"
        argv = kwargs.pop("argv", marker_executable(marker))
        kwargs.setdefault("private_root", self.root)
        try:
            launch(argv, **kwargs)
        except LaunchRejected as error:
            return str(error)
        raise AssertionError("the launch should have been rejected before spawn")

    def assert_marker_absent(self) -> None:
        self.assertFalse((self.root / "marker.txt").exists(), "rejected argv must never run")

    def test_missing_private_root_is_refused(self):
        message = self.launch_expecting_rejection(private_root=None)
        self.assertIn("private root", message)
        self.assert_marker_absent()

    def test_unprepared_homes_are_refused(self):
        empty = self.sibling_dir("empty-root")
        message = self.launch_expecting_rejection(private_root=empty,
                                                  dsh_home=empty / "dsh-home",
                                                  home=empty / "home")
        self.assertIn("does not exist", message)
        self.assert_marker_absent()

    def test_user_default_dsh_home_is_refused(self):
        message = self.launch_expecting_rejection(dsh_home=default_dsh_home())
        self.assertIn("default DSH home", message)
        self.assert_marker_absent()

    def test_symlinked_home_is_refused(self):
        link = self.root / "link-home"
        link.symlink_to(self.sibling_dir("link-target"))
        message = self.launch_expecting_rejection(dsh_home=link)
        self.assertIn("symlink", message)
        self.assert_marker_absent()

    def test_home_outside_the_private_root_is_refused(self):
        message = self.launch_expecting_rejection(home=self.sibling_dir("outside-root"))
        self.assertIn("outside the private root", message)
        self.assert_marker_absent()

    def test_user_real_home_is_refused(self):
        message = self.launch_expecting_rejection(home=real_user_home())
        self.assertIn("real home", message)
        self.assert_marker_absent()

    def test_reserved_env_keys_cannot_override_the_private_homes(self):
        for reserved in ("HOME", "DSH_HOME"):
            message = self.launch_expecting_rejection(
                extra_env={reserved: str(self.sibling_dir(f"redirect-{reserved}"))})
            self.assertIn("reserved", message)
            self.assert_marker_absent()

    def test_launch_log_outside_the_private_root_is_refused(self):
        outside_log = self.sibling_dir("outside-logs") / "launches.jsonl"
        message = self.launch_expecting_rejection(launch_log=outside_log)
        self.assertIn("outside the private root", message)
        self.assertFalse(outside_log.exists(), "no log may be written outside the private root")
        self.assert_marker_absent()

    def test_rejections_are_recorded_in_the_launch_log(self):
        self.launch_expecting_rejection(dsh_home=default_dsh_home())
        log_path = self.root / "logs" / "launches.jsonl"
        entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        self.assertEqual(1, len(entries))
        self.assertTrue(entries[0]["rejected"])
        self.assertIsNone(entries[0]["pid"])


class LegalLaunchTest(AcpTestCase):
    def test_legal_launch_runs_and_logs_the_native_environment(self):
        marker = self.root / "marker.txt"
        junk_value = "secret-junk-value"
        os.environ["ACP_TEST_JUNK"] = junk_value
        self.addCleanup(os.environ.pop, "ACP_TEST_JUNK", None)
        process, handle = launch(marker_executable(marker), private_root=self.root)
        try:
            process.communicate(timeout=30)
            self.assertEqual(0, process.returncode)
            self.assertTrue(marker.is_file(), "the legal control must have executed")
        finally:
            handle.wait(5)
        if os.name != "nt":
            self.assertEqual(process.pid, handle.pgid)
        entry = json.loads((self.root / "logs" / "launches.jsonl").read_text().splitlines()[0])
        self.assertIsNone(entry["rejected"])
        self.assertEqual(process.pid, entry["pid"])
        self.assertIn("PATH", entry["envKeys"])
        self.assertIn("DSH_HOME", entry["envKeys"])
        self.assertEqual(str(self.dsh_home), entry["dshHome"])
        self.assertIsNone(entry["home"], "no explicit home was given for this launch")
        # No environment value may appear in the launch record.
        raw = (self.root / "logs" / "launches.jsonl").read_text()
        self.assertNotIn(junk_value, raw)
        self.assertNotIn(os.environ["PATH"], raw)

    def test_child_environment_is_native_environment_plus_the_forced_homes(self):
        synthetic = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/Users/synthetic-person",
            "USER": "synthetic-person",
            "LOGNAME": "synthetic-person",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "TMPDIR": "/synthetic/tmp",
            "TERM": "xterm-256color",
            "HTTPS_PROXY": "http://proxy.example:8443",
            "NO_PROXY": "localhost",
            "SSL_CERT_FILE": "/synthetic/ca.pem",
            "NODE_EXTRA_CA_CERTS": "/synthetic/node-ca.pem",
            "BUDDY_AGENT_CREDENTIAL": "synthetic-credential-value",
            "ACP_TEST_SECRET": "synthetic-secret-value",
        }
        extras = {"DSH_PERMISSION_MODE": "read-only"}
        explicit = child_environment(self.dsh_home, self.home, extras, source=synthetic)
        expected = native_environment(dict(synthetic))
        expected.update(extras)
        expected["HOME"] = str(self.home)
        expected["DSH_HOME"] = str(self.dsh_home)
        self.assertEqual(expected, explicit,
                         "the child environment is native_environment plus this run's homes")
        self.assertEqual("synthetic-person", explicit["USER"])
        self.assertEqual("en_US.UTF-8", explicit["LANG"])
        self.assertEqual("/synthetic/tmp", explicit["TMPDIR"])
        self.assertEqual("http://proxy.example:8443", explicit["HTTPS_PROXY"])
        self.assertEqual("/synthetic/ca.pem", explicit["SSL_CERT_FILE"])
        self.assertEqual("/synthetic/node-ca.pem", explicit["NODE_EXTRA_CA_CERTS"])
        # Nothing outside the native allow-list - least of all a credential
        # variable - is brought in from the parent environment.
        self.assertNotIn("BUDDY_AGENT_CREDENTIAL", explicit)
        self.assertNotIn("ACP_TEST_SECRET", explicit)

    def test_parent_home_is_kept_without_an_explicit_private_home(self):
        synthetic = {"PATH": "/usr/bin:/bin", "HOME": "/Users/synthetic-person",
                     "TMPDIR": "/synthetic/tmp"}
        inherited = child_environment(self.dsh_home, None, source=synthetic)
        self.assertEqual("/Users/synthetic-person", inherited["HOME"],
                         "without an explicit private home the parent HOME is kept")
        self.assertEqual(str(self.dsh_home), inherited["DSH_HOME"])
        self.assertEqual(dict(native_environment(dict(synthetic)),
                              DSH_HOME=str(self.dsh_home)), inherited)

    def test_spawned_child_receives_the_native_environment_with_forced_homes(self):
        junk_value = "secret-junk-value"
        os.environ["ACP_TEST_JUNK"] = junk_value
        self.addCleanup(os.environ.pop, "ACP_TEST_JUNK", None)
        argv = [sys.executable, "-c", "import json,os;print(json.dumps(dict(os.environ)))"]
        extras = {"DSH_PERMISSION_MODE": "read-only"}
        process, handle = launch(argv, private_root=self.root, home=self.home, extra_env=extras)
        try:
            out, _ = process.communicate(timeout=30)
            self.assertEqual(0, process.returncode)
        finally:
            handle.wait(5)
        child_env = json.loads(out.decode())
        expected = child_environment(self.dsh_home, self.home, extras)
        # A child Python may re-exec itself once (PEP 538 locale coercion) and
        # CoreFoundation adds the encoding var; those are the child's own keys.
        interpreter_added = {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
        self.assertEqual(set(), set(child_env) - set(expected) - interpreter_added,
                         "the child sees exactly the computed environment")
        for key, value in expected.items():
            self.assertEqual(value, child_env[key])
        self.assertEqual(str(self.home), child_env["HOME"],
                         "the explicit private home is what the child runs under")
        self.assertNotIn("ACP_TEST_JUNK", child_env,
                         "a parent key outside the native allow-list never crosses")

    def test_post_spawn_bookkeeping_failure_keeps_ownership(self):
        marker = self.root / "marker.txt"
        with mock.patch.object(launch_module, "record_launch",
                               side_effect=RuntimeError("bookkeeping exploded")):
            with self.assertRaises(LaunchOwnershipError) as caught:
                launch(marker_executable(marker), private_root=self.root)
        error = caught.exception
        self.assertEqual(0, error.process.wait(timeout=30))
        self.assertTrue(error.evidence["shutdownConfirmed"],
                        f"the finalize path must confirm the stop: {error.evidence}")
        self.assertEqual(0, error.evidence["leaderExitCode"])
        self.assertEqual("gone", error.evidence["groupObserved"])
        del error  # the carried handle dies with the test; the group is confirmed gone

    def test_frame_log_outside_the_private_root_is_refused(self):
        from hey_my_buddy.buddy.harnesses.dsh.acp import AcpClient
        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import FrameMetaLog

        outside = self.sibling_dir("outside-frames") / "frames.jsonl"
        with self.assertRaises(LaunchRejected):
            AcpClient.start(self.agent_argv(), private_root=self.root,
                            dsh_home=self.dsh_home, home=self.home,
                            frame_log=FrameMetaLog(outside))
        self.assertFalse(outside.exists())


class LogPathSideEffectTest(AcpTestCase):
    """A rejected log path must never create, chmod or touch outside objects."""

    def test_rejected_outside_parent_is_not_created(self):
        from hey_my_buddy.buddy.harnesses.dsh.acp.launch import LaunchRejected, ensure_private_log_path

        outside = self.sibling_dir("outside-logs")
        target = outside / "never-created" / "launches.jsonl"
        with self.assertRaises(LaunchRejected):
            ensure_private_log_path(target, self.root)
        self.assertFalse(target.exists())
        self.assertFalse((outside / "never-created").exists(),
                         "the rejection must not create the outside parent")

    def test_missing_illegal_parent_chain_is_rejected_without_creation(self):
        from hey_my_buddy.buddy.harnesses.dsh.acp.launch import LaunchRejected, ensure_private_log_path

        outside = self.sibling_dir("outside-logs-2")
        (outside / "keep-0700").mkdir(mode=0o700)
        before = os.stat(outside / "keep-0700")
        target = outside / "keep-0700" / "no-such-child" / "log"
        with self.assertRaises(LaunchRejected):
            ensure_private_log_path(target, self.root)
        after = os.stat(outside / "keep-0700")
        self.assertEqual(before.st_mode, after.st_mode, "no chmod may touch a rejected path")
        self.assertFalse((outside / "keep-0700" / "no-such-child").exists())

    def test_default_and_linked_roots_are_rejected_without_side_effects(self):
        from hey_my_buddy.buddy.harnesses.dsh.acp.launch import (LaunchRejected,
                                                                default_dsh_home,
                                                                ensure_private_log_path)

        with self.assertRaises(LaunchRejected):
            ensure_private_log_path(default_dsh_home() / "logs" / "x.jsonl",
                                    default_dsh_home())
        target = self.sibling_dir("link-root-target")
        link_root = self.root.parent / "linked-root"
        link_root.symlink_to(target)
        with self.assertRaises(LaunchRejected):
            ensure_private_log_path(link_root / "logs" / "x.jsonl", link_root)
        self.assertFalse((target / "logs").exists(),
                         "a rejected linked root must gain no directory")

    def test_root_rejections_write_no_launch_log(self):
        missing = self.sibling_dir("missing-root")
        missing.rmdir()  # registered at creation; removing our own empty dir is the test setup
        with self.assertRaises(LaunchRejected):
            launch(self.agent_argv(), private_root=missing)
        target = self.sibling_dir("link-launch-target")
        link_root = self.root.parent / "linked-launch-root"
        link_root.symlink_to(target)
        with self.assertRaises(LaunchRejected):
            launch(self.agent_argv(), private_root=link_root)
        self.assertFalse((target / "logs").exists())


class FinalizeFaultTest(AcpTestCase):
    def test_finalize_faults_become_evidence_and_ownership_is_kept(self):
        marker = self.root / "marker.txt"
        with mock.patch.object(launch_module, "record_launch",
                               side_effect=RuntimeError("bookkeeping exploded")), \
             mock.patch.object(launch_module, "group_observation",
                               side_effect=OSError("observation exploded")):
            with self.assertRaises(LaunchOwnershipError) as caught:
                launch(marker_executable(marker), private_root=self.root)
        error = caught.exception
        self.assertTrue(error.evidence["finalizeErrors"],
                        "the observation fault must land in the evidence")
        self.assertIn("OSError", " ".join(error.evidence["finalizeErrors"]))
        self.assertFalse(error.evidence["shutdownConfirmed"],
                         "an unobservable group is never written down as stopped")
        self.assertEqual(0, error.process.wait(timeout=30))
        self.assertTrue(error.handle is not None and error.process is not None)


if __name__ == "__main__":  # pragma: no cover - direct execution convenience
    unittest.main()
