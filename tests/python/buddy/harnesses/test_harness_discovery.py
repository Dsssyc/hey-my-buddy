"""Private, model-free tests for the shared harness locator."""
from __future__ import annotations

from pathlib import Path
import json
import os
import plistlib
import signal
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hey_my_buddy.buddy.harnesses import discovery
from hey_my_buddy.buddy.harnesses.runtime_selection import controller_environment


_REAL_LAUNCH_COMMAND = discovery._launch_command


class HarnessDiscoveryTests(unittest.TestCase):
    @staticmethod
    def candidate_name(name: str) -> str:
        return name + ('.exe' if os.name == 'nt' else '')

    def setUp(self):
        # Discovery ranking and probe semantics use plain fixture files. The
        # four launch-chain cases exercise the actual native entry boundary.
        self.enterContext(patch.object(
            discovery, "_launch_command",
            side_effect=lambda path, _environment, **_kwargs: [sys.executable, str(path)]))
        self.enterContext(patch.object(
            discovery, "_native_command",
            side_effect=lambda path: [sys.executable, str(path)]))
        self.enterContext(patch.object(discovery, "_registry_paths", return_value=[]))
        self.enterContext(patch.object(discovery, "_app_paths", return_value=[]))

    def test_controller_keeps_credential_private_from_native_harness(self):
        source = {"HMB_STATE_DIR": "/private/board",
                  "HMB_AGENT_CREDENTIAL_FILE": "/private/attempt/credential.json",
                  "HMB_TASK_ID": "task", "HMB_ATTEMPT_ID": "attempt",
                  "HMB_RUNTIME_ROOT": "/private/runtime", "HMB_DEV_SOURCE": "1",
                  "HMB_WORKER_ID": "worker"}
        ordinary = controller_environment(Path("/private/attempt"), source)
        self.assertEqual(ordinary["HMB_AGENT_CREDENTIAL_FILE"], source["HMB_AGENT_CREDENTIAL_FILE"])
        self.assertNotIn("HMB_TASK_ID", ordinary)
        self.assertNotIn("HMB_ATTEMPT_ID", ordinary)
        read_only = controller_environment(Path("/private/attempt"), source, read_only=True)
        self.assertNotIn("HMB_AGENT_CREDENTIAL_FILE", read_only)
        self.assertNotIn("HMB_AGENT_CREDENTIAL_FILE", discovery.native_environment(source))

    def test_claude_reports_native_provider_and_unknown_billing_without_refusal(self):
        for account, provider in (({"loggedIn": True, "apiProvider": "bedrock", "authMethod": "api_key"}, "bedrock"),
                                  ({"loggedIn": True, "apiProvider": "firstParty"}, "anthropic"),
                                  ({"loggedIn": True, "apiProvider": "firstParty", "authMethod": "api_key"}, "anthropic"),
                                  ({"loggedIn": True}, "unknown")):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                cli = self.plain_file(home / "claude", "import sys\nprint('claude 2.1.0' if sys.argv[1:] == ['--version'] else "
                                      + repr(json.dumps(account)) + ")\n")
                with patch.object(discovery, "_home", return_value=home), \
                     patch.object(discovery, "_common_dirs", return_value=[]), \
                     patch.object(discovery, "_app_paths", return_value=[]):
                    result = discovery.discover("claude", manual_path=str(cli), environment={"PATH": ""}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
                self.assertEqual(result["status"], "ready")
                self.assertEqual(set(result["billingByProvider"]), {provider})
                self.assertEqual(result["billingByProvider"][provider]["kind"], "unknown")

    @staticmethod
    def plain_file(path: Path, source: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8", newline="\n")
        return path

    def test_snapshot_only_fingerprints_candidates_and_manager_records(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bin_dir = home / "bin"
            bin_dir.mkdir()
            cli = bin_dir / self.candidate_name("codex")
            cli.write_text("stub", encoding="utf-8", newline="\n")
            record = home / ".nvm/alias/default"
            record.parent.mkdir(parents=True)
            record.write_text("v24.1.0", encoding="utf-8", newline="\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ):
                snapshot = discovery.candidate_snapshot("codex", manual_path=str(cli),
                                                        environment={"PATH": str(bin_dir), "OPENAI_API_KEY": "secret"})
            self.assertEqual(snapshot["candidates"][0]["source"], "manual")
            self.assertEqual(snapshot["candidates"][0]["fingerprint"], discovery.file_fingerprint(cli))
            self.assertEqual(snapshot["managerRecords"][0]["fingerprint"], discovery.file_fingerprint(record))
            self.assertNotIn("secret", str(snapshot))
            self.assertNotIn("OPENAI_API_KEY", str(snapshot))

    def test_position_scan_uses_stat_without_opening_candidate_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bin_dir = home / 'bin'
            bin_dir.mkdir()
            for name in ('codex', 'node'):
                (bin_dir / self.candidate_name(name)).write_text('fixture', encoding="utf-8", newline="\n")
            with patch.object(discovery, '_home', return_value=home), \
                 patch.object(discovery, '_manager_records', return_value=([], [])), \
                 patch.object(discovery, '_common_dirs', return_value=[]), \
                 patch.object(discovery, '_app_paths', return_value=[]), \
                 patch.object(Path, 'open', side_effect=AssertionError('Position scans must not read candidate bytes')):
                snapshot = discovery.candidate_snapshot('codex', environment={'PATH': str(bin_dir)})
                self.assertTrue(discovery.location_fingerprint(snapshot))
            fingerprints = [item['fingerprint'] for item in snapshot['candidates'] if item['exists']]
            fingerprints += [item['fingerprint'] for item in snapshot['nodeCandidates'] if item['fingerprint']]
            self.assertTrue(fingerprints)
            self.assertTrue(all(set(value) == {'realPath', 'size', 'mtimeNs'} for value in fingerprints))

    def test_manual_failure_falls_through_and_manager_version_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bad = self.plain_file(home / "bad/codex", "import sys\nprint(\"private-output\")\nsys.exit(3)\n")
            nvm = home / ".nvm/versions/node/v24.1.0/bin" / self.candidate_name("codex")
            self.plain_file(nvm, "import sys\nprint(\"Logged in\" if sys.argv[1:] == [\"login\", \"status\"] else \"codex 1.2.0\")\n")
            fnm = home / ".fnm/node-versions/v25.0.0/installation/bin" / self.candidate_name("codex")
            self.plain_file(fnm, "import sys\nprint(\"Logged in\" if sys.argv[1:] == [\"login\", \"status\"] else \"codex 2.0.0\")\n")
            for path, value in ((home / ".nvm/alias/default", "v24.1.0"),
                                (home / ".fnm/aliases/default", "v25.0.0")):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value, encoding="utf-8", newline="\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                result = discovery.discover("codex", manual_path=str(bad), environment={"PATH": ""}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["source"], "fnm")
            self.assertEqual(result["version"], "2.0.0")
            self.assertNotIn("private-output", str(result))
            self.assertEqual(len(result["scanFingerprint"]), 64)

    @unittest.skipIf(os.name == "nt", "POSIX symlink setup")
    def test_node_companion_is_found_beside_launcher_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bin_dir = home / "bin"
            bin_dir.mkdir()
            script = home / "packages/codex.js"
            script.parent.mkdir()
            script.write_text("// npm target", encoding="utf-8", newline="\n")
            launcher = bin_dir / "codex"
            launcher.symlink_to(script)
            node = self.plain_file(bin_dir / "node", "import sys\nprint(\"codex 3.0.0\" if len(sys.argv) > 2 else \"v24.1.0\")\n")
            with patch.object(discovery, "_launch_command", _REAL_LAUNCH_COMMAND), \
                 patch.object(discovery, "probe", side_effect=lambda command, *_args, **_kwargs:
                              (None, b'v24.1.0' if str(node) in command and
                               str(launcher) not in command else b'zcode 3.0.0')) as probe, \
                 patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                result = discovery.discover("zcode", manual_path=str(launcher), environment={"PATH": ""}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["command"], [str(node), str(launcher)])
            self.assertTrue(any(call.args[0][:1] == [str(node)] for call in probe.call_args_list))

    def test_failed_login_and_overflow_keep_output_private(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cli = self.plain_file(home / "codex", "import sys\nprint(\"codex 1.0.0\" if sys.argv[1:] == [\"--version\"] else \"Not logged in private-account\")\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                result = discovery.discover("codex", manual_path=str(cli), environment={"PATH": ""}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertEqual(result["status"], "login-required")
            self.assertFalse(result["available"])
            self.assertNotIn("private-account", str(result))
            self.plain_file(cli, "print(\"secret\" * 10000)\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                result = discovery.discover("codex", manual_path=str(cli), environment={"PATH": ""}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertIn(result["reason"]["code"], ("harness.output-limit", "harness.shutdown-unverified"))
            self.assertEqual(result["status"], "unhealthy")
            self.assertFalse(result["available"])
            self.assertNotIn("secret", str(result))

            # Withhold containment evidence after actually stopping our fixture.
            # The probe must stay unavailable and must not expose its output.
            terminate = discovery._terminate_group

            def unobserved_stop(process):
                terminate(process)
                return False

            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]), patch.object(
                discovery, "_terminate_group", side_effect=unobserved_stop
            ):
                uncertain = discovery.discover("codex", manual_path=str(cli), environment={"PATH": ""}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertEqual(uncertain["reason"]["code"], "harness.shutdown-unverified")
            self.assertEqual(uncertain["status"], "unhealthy")
            self.assertFalse(uncertain["available"])
            self.assertNotIn("secret", str(uncertain))

    def test_snapshot_hash_changes_when_default_record_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            record = home / ".nvm/alias/default"
            record.parent.mkdir(parents=True)
            record.write_text("v22.0.0", encoding="utf-8", newline="\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                first = discovery.candidate_snapshot("node", environment={"PATH": ""})
                record.write_text("v24.0.0", encoding="utf-8", newline="\n")
                second = discovery.candidate_snapshot("node", environment={"PATH": ""})
            self.assertNotEqual(discovery._scan_hash(first), discovery._scan_hash(second))
            self.assertNotEqual(discovery.location_fingerprint(first), discovery.location_fingerprint(second))

    def test_location_fingerprint_ignores_empty_paths_but_tracks_present_order_and_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            a, b, empty = (home / name for name in ('a', 'b', 'empty'))
            a.mkdir()
            b.mkdir()
            empty.mkdir()
            with patch.object(discovery, '_home', return_value=home), \
                 patch.object(discovery, '_common_dirs', return_value=[]), \
                 patch.object(discovery, '_app_paths', return_value=[]), \
                 patch.object(discovery, '_registry_paths', return_value=[]):
                def fingerprint(*paths):
                    return discovery.location_fingerprint(discovery.candidate_snapshot(
                        'codex', environment={'PATH': os.pathsep.join(map(str, paths))}))

                missing = fingerprint(a, b)
                self.assertEqual(missing, fingerprint(b, empty, a))
                cli = a / self.candidate_name('codex')
                cli.write_text('one', encoding="utf-8", newline="\n")
                appeared = fingerprint(a, b)
                self.assertNotEqual(missing, appeared)
                self.assertEqual(appeared, fingerprint(empty, b, a))
                cli.write_text('changed-size', encoding="utf-8", newline="\n")
                changed = fingerprint(a, b)
                self.assertNotEqual(appeared, changed)
                (b / self.candidate_name('codex')).write_text('other', encoding="utf-8", newline="\n")
                self.assertNotEqual(fingerprint(a, b), fingerprint(b, a))
                (b / self.candidate_name('codex')).unlink()
                cli.unlink()
                self.assertNotEqual(changed, fingerprint(a, b))
                self.assertEqual(missing, fingerprint(a, b))

    def test_empty_paths_do_not_consume_candidate_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cli = home / 'bin' / self.candidate_name('codex')
            cli.parent.mkdir()
            cli.write_text('fixture', encoding="utf-8", newline="\n")
            paths = [str(home / f'empty-{index}') for index in range(discovery._MAX_CANDIDATES + 1)]
            with patch.object(discovery, '_home', return_value=home), \
                 patch.object(discovery, '_common_dirs', return_value=[]), \
                 patch.object(discovery, '_app_paths', return_value=[]):
                first = discovery.candidate_snapshot('codex', environment={'PATH': str(cli.parent)})
                second = discovery.candidate_snapshot('codex', environment={
                    'PATH': os.pathsep.join([*paths, str(cli.parent)])})
                with patch.object(discovery, '_candidate_handshake', return_value={
                        'path': str(cli), 'source': 'path', 'status': 'ready', 'reason': None,
                        'command': [str(cli)], 'version': '1.0', 'fingerprint': {}}) as handshake:
                    result = discovery.discover('codex', environment={
                        'PATH': os.pathsep.join([*paths, str(cli.parent)])}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertEqual(discovery.location_fingerprint(first), discovery.location_fingerprint(second))
            self.assertGreater(len(second['candidates']), discovery._MAX_CANDIDATES)
            handshake.assert_called_once()
            self.assertEqual(result['status'], 'ready')

    def test_cli_scan_changes_when_adjacent_node_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cli = home / "bin" / self.candidate_name("codex")
            node = home / "bin" / self.candidate_name("node")
            cli.parent.mkdir()
            cli.write_text("#!/usr/bin/env node\n", encoding="utf-8", newline="\n")
            node.write_text("node-one", encoding="utf-8", newline="\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                first = discovery.candidate_snapshot("codex", manual_path=str(cli), environment={"PATH": ""})
                node.write_text("node-two-with-new-size", encoding="utf-8", newline="\n")
                second = discovery.candidate_snapshot("codex", manual_path=str(cli), environment={"PATH": ""})
            self.assertNotEqual(discovery._scan_hash(first), discovery._scan_hash(second))
            self.assertNotEqual(discovery.location_fingerprint(first), discovery.location_fingerprint(second))

    def test_claude_login_check_and_dsh_missing_without_native_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            claude = self.plain_file(home / "claude", "import sys\nprint(\"claude 2.1.0\" if sys.argv[1:] == [\"--version\"] else \'{\"loggedIn\":true,\"apiProvider\":\"anthropic\"}\')\n")
            node = self.plain_file(home / "bin" / self.candidate_name("node"), "import sys\nprint(\"v24.2.0\" if sys.argv[1:] == [\"--version\"] else \"checked\")\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[node.parent]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                claude_result = discovery.discover("claude", manual_path=str(claude), environment={"PATH": ""}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
                dsh_result = discovery.discover("dsh", environment={"PATH": ""}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
                node_result = discovery.discover("node", manual_path=str(node), environment={"PATH": ""}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertEqual(claude_result["status"], "ready")
            self.assertEqual(dsh_result["status"], "missing")
            self.assertFalse(dsh_result["available"])
            self.assertEqual(node_result["status"], "ready")

    def test_native_dsh_cli_requires_its_own_version_handshake(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            dsh = self.plain_file(home / "dsh", 'print("dsh 1.2.0")\n')
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                result = discovery.discover("dsh", manual_path=str(dsh), environment={"PATH": ""}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["executable"], str(dsh))

    def test_dsh_version_probe_preserves_the_users_native_home_and_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            daily = root / "daily-dsh"
            daily.mkdir()
            snapshot = root / "observed.json"
            cli = self.plain_file(root / "dsh", (
                "import json, os, sys\nfrom pathlib import Path\n"
                "assert sys.argv[1:] == ['--version']\n"
                "assert sys.stdin.read() == ''\n"
                "private = Path(os.environ['DSH_HOME'])\n"
                "assert private.is_dir()\n"
                "(private / 'probe-marker').write_text('owned', encoding='utf-8', newline='\\n')\n"
                f"Path({str(snapshot)!r}).write_text(json.dumps({{'dshHome': str(private), "
                "'home': os.environ.get('HOME'), 'proxy': os.environ.get('HTTPS_PROXY'), "
                "'ca': os.environ.get('SSL_CERT_FILE'), 'keys': sorted(os.environ)}), encoding='utf-8', newline='\\n')\n"
                "print('dsh 1.2.0', file=sys.stderr)\n"))
            environment = {
                "PATH": "", "HOME": str(root), "DSH_HOME": str(daily),
                "HTTPS_PROXY": "https://proxy.invalid", "SSL_CERT_FILE": str(root / "ca.pem"),
                "OPENAI_API_KEY": "fixture-secret"}
            with patch.object(discovery, "_home", return_value=root), \
                 patch.object(discovery, "_common_dirs", return_value=[]), \
                 patch.object(discovery, "_app_paths", return_value=[]):
                result = discovery.discover("dsh", manual_path=str(cli), environment=environment, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["version"], "1.2.0")
            observed = json.loads(snapshot.read_text(encoding="utf-8"))
            self.assertEqual(observed["dshHome"], str(daily))
            self.assertEqual(observed["home"], str(root))
            self.assertEqual(observed["proxy"], "https://proxy.invalid")
            self.assertEqual(observed["ca"], str(root / "ca.pem"))
            self.assertNotIn("OPENAI_API_KEY", observed["keys"])
            self.assertEqual((daily / "probe-marker").read_text(encoding="utf-8"), "owned")

    @unittest.skipIf(os.name == "nt", "POSIX process-group observation")
    def test_dsh_version_probe_retains_its_directory_when_stop_is_unknown(self):
        from hey_my_buddy.buddy.harnesses.base import ProcessHandle

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            private = root / "probe"
            private.mkdir()
            pid_file = root / "pid"
            cli = self.plain_file(root / "dsh", (
                "import os\nfrom pathlib import Path\n"
                f"Path({str(pid_file)!r}).write_text(str(os.getpid()), encoding='utf-8', newline='\\n')\n"
                "print('dsh 1.2.0')\n"))
            with patch.object(discovery.private_dirs, "make_temporary_dir", return_value=private), \
                 patch.object(ProcessHandle, "shutdown_confirmed", return_value=False), \
                 self.assertLogs(discovery.__name__, level="WARNING") as notes:
                code, output = discovery._probe_dsh_version([sys.executable, str(cli)], {"PATH": ""},
                                                            time.monotonic() + 5, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertEqual((code, output), ("shutdown-unverified", b""))
            self.assertFalse((private / "dsh-home").exists())
            self.assertIn(str(private), notes.output[0])
            # The injected uncertainty retains the directory. The test itself
            # verifies the actual group is gone before its fixture cleans up.
            with self.assertRaises(ProcessLookupError):
                os.killpg(int(pid_file.read_text(encoding="utf-8")), 0)

    def test_partial_manager_defaults_match_installed_versions_and_direct_root(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            defaults = {
                home / ".nvm/alias/default": "lts/*",
                home / ".nvm/alias/lts/*": "24.2",
                home / ".fnm/aliases/default": "24",
                home / ".tool-versions": "nodejs 24",
                home / ".config/mise/config.toml": '[tools]\nnode = "24"\n',
            }
            for path, value in defaults.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value, encoding="utf-8", newline="\n")
            locations = [
                home / ".nvm/versions/node/v24.2.1/bin" / self.candidate_name('codex'),
                home / ".nvm/versions/node/v24.2.10/bin" / self.candidate_name('codex'),
                home / ".fnm/node-versions/v24.3.0/installation/bin" / self.candidate_name('codex'),
                home / ".asdf/installs/nodejs/24.4.0/bin" / self.candidate_name('codex'),
                home / ".local/share/mise/installs/node/24.5.0/bin" / self.candidate_name('codex'),
            ]
            for location in locations:
                location.parent.mkdir(parents=True, exist_ok=True)
                location.write_text("stub", encoding="utf-8", newline="\n")
            direct_node = home / ".fnm/node-versions/v24.3.0/installation" / self.candidate_name('node')
            direct_node.write_text("stub", encoding="utf-8", newline="\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                snapshot = discovery.candidate_snapshot("codex", environment={"PATH": ""})
                node_snapshot = discovery.candidate_snapshot("node", environment={"PATH": ""})
            found = [item["path"] for item in snapshot["candidates"] if item["exists"]]
            self.assertTrue(all(str(path) in found for path in locations))
            nvm = [path for path in found if "/.nvm/" in path]
            self.assertEqual(nvm[:2], [str(locations[1]), str(locations[0])])
            self.assertIn(str(direct_node), [item["path"] for item in node_snapshot["candidates"]])
            self.assertIn(str(home / ".nvm/alias/lts/*"),
                          [item["path"] for item in snapshot["managerRecords"]])
            (home / ".nvm/alias/default").write_text("node", encoding="utf-8", newline="\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                generic = discovery.candidate_snapshot("codex", environment={"PATH": ""})
            self.assertIn(str(locations[1]), [item["path"] for item in generic["candidates"]])

    @unittest.skipIf(os.name == "nt", "Symlink creation needs extra Windows privileges")
    def test_nvm_alias_can_follow_user_configured_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            alias = home / ".nvm/alias"
            alias.mkdir(parents=True)
            (alias / "default").write_text("current", encoding="utf-8")
            target = home / "node-alias"
            target.write_text("24.2", encoding="utf-8")
            (alias / "current").symlink_to(target)
            installed = home / ".nvm/versions/node/v24.2.1/bin"
            installed.mkdir(parents=True)
            records, roots = discovery._manager_records(home)
        self.assertIn(str(alias / "current"), [item["path"] for item in records])
        self.assertIn(("nvm", installed), roots)

    def test_discovery_keeps_the_candidate_probe_bound(self):
        candidates = [{"path": f"/fixture/{index}", "source": "path", "exists": True,
                       "fingerprint": None} for index in range(100)]
        snapshot = {"harness": "codex", "candidates": candidates, "managerRecords": []}

        def handshake(_harness, item, _environment, _deadline, *, version_probe):
            return {**item, "status": "unhealthy", "reason": discovery._probe_reason("probe-failed"),
                    "command": [], "version": None}

        with patch.object(discovery, "candidate_snapshot", return_value=snapshot), \
             patch.object(discovery, "_candidate_handshake", side_effect=handshake) as probe:
            result = discovery.discover("codex", environment={"PATH": ""}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
        self.assertEqual(probe.call_count, discovery._MAX_CANDIDATES)
        self.assertEqual(len(result["candidates"]), discovery._MAX_DIAGNOSTICS)

    def test_manual_ready_skips_lower_priority_and_unknown_version_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cli = self.plain_file(home / "manual/zcode", 'print("release next: private detail")\n')
            marker = home / "lower-ran"
            lower = self.plain_file(home / "common" / self.candidate_name('zcode'), f'from pathlib import Path\nPath({str(marker)!r}).write_text("ran", encoding="utf-8", newline="\\n")\nprint("v9.0.0")\n')
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[lower.parent]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                result = discovery.discover("zcode", manual_path=str(cli), environment={"PATH": ""}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["version"], "unknown")
            self.assertEqual(result["reason"]["code"], "harness.version-unknown")
            self.assertFalse(marker.exists())
            self.assertNotIn("private detail", str(result))

    @unittest.skipIf(os.name == "nt", "POSIX process-group assertion")
    def test_probe_timeout_reaps_child_group_and_scan_has_total_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            child = 'import time; time.sleep(10)'
            cli = self.plain_file(home / "zcode", f'import subprocess, sys, time\nsubprocess.Popen([sys.executable, "-c", {child!r}], stdout=sys.stdout)\ntime.sleep(10)\n')
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]), patch.object(
                discovery, "_SCAN_TIMEOUT", 0.4
            ), patch.object(discovery.os, "killpg", wraps=os.killpg) as kill_group:
                started = time.monotonic()
                result = discovery.discover("zcode", manual_path=str(cli), environment={"PATH": ""}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(result["status"], "unhealthy" if result["reason"]["code"] == "harness.shutdown-unverified" else "unknown")
            # A slow OS reaper may not close the inherited pipe within the
            # cleanup budget. It must remain unavailable with honest evidence.
            self.assertIn(result["reason"]["code"], ("harness.scan-timeout", "harness.shutdown-unverified"))
            self.assertIn(signal.SIGTERM, [call.args[1] for call in kill_group.call_args_list])
            groups = {call.args[0] for call in kill_group.call_args_list}
            self.assertTrue(groups)
            for pgid in groups:
                with self.assertRaises(ProcessLookupError):
                    os.killpg(pgid, 0)

    @unittest.skipIf(os.name == "nt", "POSIX process-group assertion")
    def test_total_deadline_covers_multiple_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            manual = self.plain_file(home / "manual/zcode", "import time\ntime.sleep(10)\n")
            lower = self.plain_file(home / "common" / self.candidate_name('zcode'), "import time\ntime.sleep(10)\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[lower.parent]
            ), patch.object(discovery, "_app_paths", return_value=[]), patch.object(
                discovery, "_TIMEOUT", 0.25
            ), patch.object(discovery, "_SCAN_TIMEOUT", 0.45):
                started = time.monotonic()
                result = discovery.discover("zcode", manual_path=str(manual), environment={"PATH": ""}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertEqual(result["reason"]["code"], "harness.scan-timeout")
            self.assertEqual(len(result["candidates"]), 2)

    def test_a_later_unknown_node_scan_is_not_replaced_by_an_earlier_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = self.plain_file(root / 'codex.js', 'print("fixture")\n')
            node = self.plain_file(root / self.candidate_name('node'), 'print("fixture")\n')
            candidate = {'path': str(launcher), 'source': 'manual', 'exists': True,
                         'fingerprint': discovery.file_fingerprint(launcher)}
            with patch.object(discovery, '_launch_command', _REAL_LAUNCH_COMMAND), \
                 patch.object(discovery, '_variants', return_value=[node]), \
                 patch.object(discovery, 'probe', return_value=('probe-failed', b'')), \
                 patch.object(discovery, '_discover', return_value={'status': 'unknown', 'available': False,
                                                                  'reason': {'code': 'harness.scan-timeout', 'params': {'operation': 'probe'}}}):
                result = discovery._candidate_handshake('codex', candidate, {'PATH': ''}, time.monotonic() + 20, version_probe=discovery._probe_cli_version)
            self.assertEqual(result['status'], 'unknown')
            self.assertEqual(result['reason']['code'], 'harness.scan-timeout')

    def test_native_environment_does_not_enumerate_values(self):
        class NoEnumeration(dict):
            def items(self):
                raise AssertionError("environment enumeration forbidden")

        environment = NoEnumeration({"PATH": "/usr/bin", "HOME": "/home/user",
                                     "LC_CTYPE": "en_US.UTF-8", "LC_SECRET_TOKEN": "private",
                                     "OPENAI_API_KEY": "private", "HMB_AGENT_CREDENTIAL_FILE": "/private/attempt",
                                     "ANTHROPIC_BASE_URL": "third-party"})
        result = discovery.native_environment(environment)
        self.assertEqual(result["LC_CTYPE"], "en_US.UTF-8")
        self.assertNotIn("LC_SECRET_TOKEN", result)
        self.assertNotIn("OPENAI_API_KEY", result)
        self.assertNotIn("ANTHROPIC_BASE_URL", result)
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "codex"
            cli.write_text("stub", encoding="utf-8", newline="\n")
            path = discovery.native_environment({"PATH": ""}, command=[str(cli)])["PATH"]
            self.assertEqual(path, str(cli.parent))

    def test_plain_powershell_file_is_not_a_discovery_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "claude.ps1").write_text("Write-Output never-run", encoding="utf-8", newline="\n")
            with patch.object(discovery, "_home", return_value=root), \
                 patch.object(discovery, "_common_dirs", return_value=[]), \
                 patch.object(discovery, "_app_paths", return_value=[]):
                snapshot = discovery.candidate_snapshot("claude", environment={"PATH": str(root)})
        self.assertNotIn(str(root / "claude.ps1"),
                         [candidate["path"] for candidate in snapshot["candidates"]])

    def test_native_environment_is_allowlisted_and_command_prepends_bins(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "codex"
            cli.write_text("stub", encoding="utf-8", newline="\n")
            result = discovery.native_environment({"PATH": "/usr/bin", "HOME": "/home/user",
                                                   "HTTPS_PROXY": "proxy", "OPENAI_API_KEY": "secret",
                                                   "ANTHROPIC_BASE_URL": "third-party",
                                                   "HMB_AGENT_CREDENTIAL_FILE": "/private/attempt"}, command=[str(cli)])
            self.assertEqual(result["PATH"].split(":")[0], str(cli.parent))
            self.assertEqual(result["HOME"], "/home/user")
            self.assertNotIn("OPENAI_API_KEY", result)
            self.assertNotIn("ANTHROPIC_BASE_URL", result)
            self.assertNotIn("HMB_AGENT_CREDENTIAL_FILE", result)

    def test_native_environment_preserves_the_users_dsh_home_verbatim(self):
        source = {"HOME": "/users/fixture", "DSH_HOME": "/users/fixture/config.v2 用户 设置",
                  "DSH_PERMISSION_MODE": "danger-full-access", "DEEPSEEK_API_KEY": "fixture-secret"}
        values = discovery.native_environment(source)
        self.assertEqual(values, {"HOME": source["HOME"], "DSH_HOME": source["DSH_HOME"], "PATH": ""})
        self.assertNotIn("DSH_HOME", discovery.native_environment({"HOME": source["HOME"]}))

    def test_native_environment_keeps_the_login_name_for_keychain_logins(self):
        result = discovery.native_environment({"PATH": "/usr/bin", "HOME": "/home/user", "USER": "user",
                                               "LOGNAME": "user", "USERNAME": "user",
                                               "ANTHROPIC_API_KEY": "secret"})
        self.assertEqual((result["USER"], result["LOGNAME"], result["USERNAME"]), ("user", "user", "user"))
        self.assertNotIn("ANTHROPIC_API_KEY", result)

    def test_native_environment_keeps_ca_certificate_paths(self):
        # Certificate paths name trust roots, not credentials; everything else
        # outside the allowlist (provider keys, hey-my-buddy state) stays filtered.
        certificates = {"SSL_CERT_FILE": "/certs/root.pem", "SSL_CERT_DIR": "/certs/dirs",
                        "REQUESTS_CA_BUNDLE": "/certs/bundle.pem", "CURL_CA_BUNDLE": "/certs/curl.pem",
                        "NODE_EXTRA_CA_CERTS": "/certs/node.pem"}
        result = discovery.native_environment({"PATH": "/usr/bin", "HOME": "/home/user",
                                               "OPENAI_API_KEY": "secret", "HMB_STATE_DIR": "/private/state",
                                               **certificates})
        self.assertEqual({key: result.get(key) for key in certificates}, certificates)
        self.assertNotIn("OPENAI_API_KEY", result)
        self.assertNotIn("HMB_STATE_DIR", result)


class WindowsEntryBoundaryTests(unittest.TestCase):
    def test_fixed_windows_candidates_ignore_pathext_and_scripts(self):
        directory = Path('/fixture/bin')
        with patch.object(discovery, 'os', SimpleNamespace(name='nt')):
            expected = [directory / 'codex.exe', directory / 'codex.cmd']
            for pathext in ('', '.BAT;.COM;.PS1;.JS;.CMD;.EXE', '.EXE;.CMD'):
                with self.subTest(pathext=pathext), patch.dict(os.environ, {'PATHEXT': pathext}):
                    self.assertEqual(discovery._variants(directory, 'codex'), expected)
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(discovery._variants(directory, 'codex'), expected)
        with tempfile.TemporaryDirectory() as root:
            for name in ('codex.ps1', 'codex.bat', 'codex.com', 'codex'):
                (Path(root) / name).write_text('ignored', encoding="utf-8", newline="\n")
            with patch.object(discovery, '_variants',
                              side_effect=lambda directory, name: [directory / (name + '.exe'),
                                                                    directory / (name + '.cmd')]):
                self.assertFalse(discovery.has_candidate(Path(root)))

    def test_windows_common_dirs_follow_environment_roots(self):
        home = Path('/private/home')
        env = {'APPDATA': '/different/Roaming', 'LOCALAPPDATA': '/different/Local',
               'ProgramFiles': '/programs', 'PATH': '/irrelevant'}
        with patch.object(discovery, 'os', SimpleNamespace(name='nt')):
            self.assertEqual(discovery._common_dirs(home, env), [
                Path('/different/Roaming/npm'), Path('/different/Local/Programs/nodejs'),
                Path('/programs/nodejs'), home / '.local/bin'])
            self.assertEqual(discovery._common_dirs(home, {}), [home / '.local/bin'])

    def test_windows_node_and_manual_scripts_require_native_exe(self):
        root = Path('/fixture/bin')
        with patch.object(discovery, 'os', SimpleNamespace(name='nt')):
            self.assertEqual(discovery._native_command(root / 'node.exe'),
                             [str(root / 'node.exe')])
            for suffix in ('.cmd', '.bat', '.ps1', '.com', ''):
                with self.subTest(suffix=suffix), self.assertRaises(discovery.EntryUnsupported):
                    discovery._native_command(root / ('node' + suffix))

    def test_windows_manual_script_is_rejected_before_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('codex.cmd', 'codex.bat', 'codex.ps1', 'codex.com', 'codex'):
                entry = root / name
                entry.write_text('do not execute', encoding="utf-8", newline="\n")
                candidate = {'path': str(entry), 'source': 'manual', 'exists': True,
                             'fingerprint': discovery.file_fingerprint(entry)}
                with self.subTest(name=name), \
                     patch.object(discovery, 'os', SimpleNamespace(name='nt')), \
                     patch.object(discovery, 'probe') as probe, \
                     patch.object(discovery, '_node_for') as node_for:
                    result = discovery._candidate_handshake('codex', candidate, {'PATH': ''},
                                                            time.monotonic() + 1, version_probe=discovery._probe_cli_version)
                self.assertEqual(result['reason']['code'], 'harness.entry-unsupported')
                probe.assert_not_called()
                node_for.assert_not_called()

    def test_windows_entry_suffix_is_not_reinterpreted_through_a_symlink(self):
        with patch.object(discovery, 'os', SimpleNamespace(name='nt')), \
             patch.object(Path, 'resolve', side_effect=AssertionError('entry type is already known')):
            for suffix in ('.exe', '.bat', '.ps1', '.com', ''):
                with self.subTest(suffix=suffix):
                    self.assertIsNone(discovery._script_launch(Path('/fixture/cli' + suffix)))

    def test_shim_file_change_invalidates_location_fingerprint_without_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / 'codex.cmd'
            shim.write_text('first shim', encoding="utf-8", newline="\n")
            with patch.object(discovery, '_home', return_value=root), \
                 patch.object(discovery, '_manager_records', return_value=([], [])), \
                 patch.object(discovery, '_common_dirs', return_value=[]), \
                 patch.object(discovery, '_app_paths', return_value=[]), \
                 patch.object(discovery, '_registry_paths', return_value=[]), \
                 patch.object(discovery, '_record_text', side_effect=AssertionError('snapshot must only stat')):
                first = discovery.candidate_snapshot('codex', manual_path=str(shim), environment={'PATH': ''})
                shim.write_text('changed shim with a different size', encoding="utf-8", newline="\n")
                second = discovery.candidate_snapshot('codex', manual_path=str(shim), environment={'PATH': ''})
            self.assertNotEqual(discovery.location_fingerprint(first), discovery.location_fingerprint(second))

    def test_windows_shim_prefers_adjacent_native_node_then_path_node(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / 'codex.cmd'
            target = root / 'lib/cli.js'
            target.parent.mkdir()
            target.write_text('fixture', encoding="utf-8", newline="\n")
            shim.write_text('@"node" "%~dp0\\lib\\cli.js" %*', encoding="utf-8", newline="\n")
            adjacent = root / 'node.exe'
            adjacent.write_text('native placeholder', encoding="utf-8", newline="\n")
            windows = SimpleNamespace(name='nt', pathsep=os.pathsep)
            with patch.object(discovery, 'os', windows), \
                 patch.object(discovery, 'probe', return_value=(None, b'v24.1.0')) as probe:
                self.assertEqual(discovery._launch_command(shim, {'PATH': ''}),
                                 [str(adjacent), str(target.resolve())])
            self.assertEqual(probe.call_count, 1)
            adjacent.unlink()
            path_node = root / 'other/node.exe'
            path_node.parent.mkdir()
            path_node.write_text('native placeholder', encoding="utf-8", newline="\n")
            found = {'status': 'ready', 'available': True, 'executable': str(path_node),
                     'version': '24.1.0', 'reason': None}
            with patch.object(discovery, 'os', windows), \
                 patch.object(discovery, '_discover', return_value=found):
                self.assertEqual(discovery._launch_command(shim, {'PATH': str(path_node.parent)}),
                                 [str(path_node), str(target.resolve())])
            absent = {'status': 'missing', 'available': False,
                      'reason': {'code': 'harness.not-found', 'params': {'operation': 'probe'}}}
            with patch.object(discovery, 'os', windows), \
                 patch.object(discovery, '_discover', return_value=absent), \
                 self.assertRaises(discovery.EntryUnavailable) as caught:
                discovery._launch_command(shim, {'PATH': ''})
            self.assertEqual(caught.exception.code, 'node-missing')

    def test_registry_expands_only_expand_sz(self):
        class Registry:
            HKEY_CURRENT_USER = 1
            HKEY_LOCAL_MACHINE = 2
            REG_EXPAND_SZ = 3
            REG_SZ = 4

            @staticmethod
            def OpenKey(hive, _key):
                class Handle:
                    def __enter__(self):
                        return hive

                    def __exit__(self, *_args):
                        return None

                return Handle()

            @staticmethod
            def QueryValueEx(handle, _name):
                return ('%APPDATA%/npm', Registry.REG_EXPAND_SZ) if handle == 1 else (
                    '%APPDATA%/literal', Registry.REG_SZ)

            @staticmethod
            def ExpandEnvironmentStrings(value):
                return value.replace('%APPDATA%', '/actual/Roaming')

        with patch.object(discovery, 'os', SimpleNamespace(name='nt', pathsep=os.pathsep)), \
             patch.dict(sys.modules, {'winreg': Registry}):
            self.assertEqual(discovery._registry_paths(), ['/actual/Roaming/npm',
                                                          '%APPDATA%/literal'])

    def test_native_path_prefix_is_only_executed_program(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node = root / 'node/bin/node.exe'
            js = root / 'package/bin/codex.js'
            for path in (node, js):
                path.parent.mkdir(parents=True)
                path.write_text('fixture', encoding="utf-8", newline="\n")
            source = {'PATH': '/user/bin', 'PATHEXT': '.EXE;.JS'}
            selected = discovery.native_environment(source, command=[str(node), str(js)])
            self.assertEqual(selected['PATH'], os.pathsep.join([str(node.parent), '/user/bin']))
            self.assertEqual(selected['PATHEXT'], source['PATHEXT'])
            self.assertEqual(discovery.native_environment(source, command=[str(sys.executable), str(js)])['PATH'],
                             os.pathsep.join([str(Path(sys.executable).parent), '/user/bin']))

    @unittest.skipIf(os.name == 'nt', 'POSIX Node entry paths')
    def test_posix_node_argv_preserves_the_selected_entry_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'package/cli.js'
            script.parent.mkdir()
            script.write_text('fixture JS', encoding="utf-8", newline="\n")
            node_script = root / 'package/node-tool'
            node_script.write_text('#!/usr/bin/env node\n', encoding="utf-8", newline="\n")
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            js_link = bin_dir / 'linked-js'
            js_link.symlink_to(script)
            node_link = bin_dir / 'linked-node'
            node_link.symlink_to(node_script)
            with patch.object(discovery, '_node_for', return_value=('/fixture/node', '24.1.0')):
                for entry in (script, js_link, node_link):
                    with self.subTest(entry=entry):
                        command = discovery._launch_command(entry, {})
                        self.assertEqual(command, ['/fixture/node', str(entry)])

    def test_npm_shim_extracts_only_one_existing_quoted_node_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'lib/cli.js'
            target.parent.mkdir()
            target.write_text('fixture', encoding="utf-8", newline="\n")
            shim = root / 'codex.cmd'
            valid = (
                '@"node" "%~dp0\\lib\\cli.js" %*',
                '@"node" "%dp0%\\lib\\cli.js" %*',
                'set "SOME_UNRELATED_FLAG=1"\n@"node" "%~dp0\\lib\\cli.js" %*',
                'set "node_exe=%~dp0\\node.exe"\n@"%node_exe%" "%~dp0\\lib\\cli.js" %*',
            )
            for content in valid:
                with self.subTest(content=content):
                    shim.write_text(content, encoding="utf-8", newline="\n")
                    self.assertEqual(discovery._script_launch(shim).arguments, [str(target.resolve())])
            special = root / 'lib (测试)&^/cli.js'
            special.parent.mkdir()
            special.write_text('fixture', encoding="utf-8", newline="\n")
            shim.write_text('@"node" "%~dp0\\lib (测试)&^\\cli.js" %*', encoding="utf-8", newline="\n")
            self.assertEqual(discovery._script_launch(shim).arguments, [str(special.resolve())])
            invalid = (
                '@"node" %~dp0\\lib\\cli.js %*',
                '@"node" "%~dp0\\missing.js" %*',
                '@python "%~dp0\\lib\\cli.js" %*',
                '@"node" "%~dp0\\lib\\cli.js" "%~dp0\\lib\\cli.js" %*',
                '@"node" "%~dp0\\lib\\cli.js"\n@"node" "%~dp0\\lib\\cli.js"',
                'set "SOME_UNRELATED_FLAG=1"',
                'x' * 4097,
            )
            for content in invalid:
                with self.subTest(content=content):
                    shim.write_text(content, encoding="utf-8", newline="\n")
                    with self.assertRaises(discovery.EntryUnsupported):
                        discovery._script_launch(shim)
            shim.write_text('@python "%~dp0\\lib\\cli.js" %*', encoding="utf-8", newline="\n")
            candidate = {'path': str(shim), 'source': 'manual', 'exists': True,
                         'fingerprint': discovery.file_fingerprint(shim)}
            with patch.object(discovery, 'probe') as probe, \
                 patch.object(discovery, '_node_for') as node_for:
                rejected = discovery._candidate_handshake('codex', candidate, {'PATH': ''},
                                                          time.monotonic() + 5, version_probe=discovery._probe_cli_version)
            self.assertEqual(rejected['reason']['code'], 'harness.entry-unsupported')
            self.assertEqual(rejected['command'], [])
            probe.assert_not_called()
            node_for.assert_not_called()


class RegisteredAppDiscoveryTests(unittest.TestCase):
    def test_every_registered_harness_declares_cli_and_all_platforms(self):
        from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS
        from hey_my_buddy.buddy.harnesses.discovery_spec import AppEntry

        self.assertEqual(set(RUN_SEAMS), set(discovery.HARNESS_NAMES))
        for name in RUN_SEAMS:
            with self.subTest(harness=name):
                spec = discovery._discovery_spec(name)
                self.assertTrue(spec.command)
                self.assertEqual(set(spec.apps), {"darwin", "win32", "linux"})
                for platform, entry in spec.apps.items():
                    if entry is not None:
                        self.assertIsInstance(entry, AppEntry)
                        self.assertIn(entry.kind, {"bundle"} if platform == "darwin" else {"uninstall", "msix"})
                        self.assertTrue(entry.identity)
                        self.assertTrue(entry.cli_path)

    def test_macos_recognizes_bundle_id_at_top_level_in_both_roots(self):
        entry = discovery._discovery_spec("codex").apps["darwin"]
        with tempfile.TemporaryDirectory() as directory:
            roots = (Path(directory) / "system", Path(directory) / "user")
            expected = []
            for root in roots:
                for name, identity in (("Renamed.app", entry.identity), ("Codex.app", "another.bundle"),
                                       ("nested/Hidden.app", entry.identity)):
                    bundle = root / name
                    (bundle / "Contents").mkdir(parents=True)
                    with (bundle / "Contents/Info.plist").open("wb") as stream:
                        plistlib.dump({"CFBundleIdentifier": identity}, stream)
                    if name == "Renamed.app":
                        expected.append(bundle / entry.cli_path)
                broken = root / "Broken.app/Contents"
                broken.mkdir(parents=True)
                (broken / "Info.plist").write_bytes(b"<plist><broken>")
                (root / "Missing.app").mkdir()
            self.assertEqual(discovery._mac_app_paths(entry, roots), expected)
            self.assertEqual(discovery._mac_app_paths(entry, (Path(directory) / "absent",)), [])
            with patch.object(discovery.sys, "platform", "darwin"), \
                 patch.object(discovery, "_mac_app_paths", return_value=expected) as scan, \
                 patch.object(discovery, "_home", return_value=roots[1].parent):
                self.assertEqual(discovery._app_paths("codex"), expected)
            scan.assert_called_once_with(entry, (Path("/Applications"), roots[1].parent / "Applications"))

    @staticmethod
    def registry(values, children=()):
        class Handle:
            def __init__(self, key):
                self.key = key
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass

        calls = []
        def open_key(hive, key, reserved=0, access=1):
            identity = (hive, key, access)
            calls.append(identity)
            if identity not in values:
                raise FileNotFoundError(key)
            return Handle(identity)
        return SimpleNamespace(
            HKEY_CURRENT_USER="user", HKEY_LOCAL_MACHINE="machine", KEY_READ=1,
            KEY_WOW64_64KEY=256, KEY_WOW64_32KEY=512, OpenKey=open_key,
            QueryValueEx=lambda handle, name: (values[handle.key][name], 1),
            QueryInfoKey=lambda _handle: (len(children), 0, 0),
            EnumKey=lambda _handle, index: children[index], calls=calls)

    def test_windows_uninstall_reads_both_hives_and_registry_views(self):
        entry = discovery._discovery_spec("dsh").apps["win32"]
        key = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{entry.identity}"
        with tempfile.TemporaryDirectory() as directory:
            locations = [Path(directory) / str(index) for index in range(4)]
            identities = [(hive, key, 1 | view) for hive in ("user", "machine") for view in (256, 512)]
            registry = self.registry({identity: {"InstallLocation": str(path)}
                                      for identity, path in zip(identities, locations)})
            self.assertEqual(discovery._windows_app_paths(entry, registry),
                             [path / entry.cli_path for path in locations])
            self.assertEqual(registry.calls, identities)
        self.assertEqual(discovery._windows_app_paths(entry, self.registry({})), [])
        registry = self.registry({identities[0]: {"InstallLocation": ""},
                                  identities[1]: {"InstallLocation": 42}})
        self.assertEqual(discovery._windows_app_paths(entry, registry), [])

    def test_windows_msix_matches_exact_family_in_current_user_repository(self):
        entry = discovery._discovery_spec("codex").apps["win32"]
        key = (r"Software\Classes\Local Settings\Software\Microsoft\Windows"
               r"\CurrentVersion\AppModel\Repository\Packages")
        name = "OpenAI.Codex_26.1016.6573.0_x64__2p2nqsd0c76g0"
        children = ("OpenAI.Codex_1.0_x64__other", name,
                    "Other.App_1.0_x64__2p2nqsd0c76g0", "malformed")
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory) / "Package"
            registry = self.registry({("user", key, 1): {},
                                      ("user", key + "\\" + name, 1): {"PackageRootFolder": str(location)}},
                                     children)
            expected = [location / entry.cli_path]
            self.assertEqual(discovery._windows_app_paths(entry, registry), expected)
            self.assertEqual(registry.calls, [("user", key, 1), ("user", key + "\\" + name, 1)])
            with patch.object(discovery.sys, "platform", "win32"), \
                 patch.dict(sys.modules, {"winreg": registry}):
                self.assertEqual(discovery._app_paths("codex"), expected)
        self.assertEqual(discovery._windows_app_paths(entry, self.registry({})), [])
        registry = self.registry({("user", key, 1): {}}, (name,))
        self.assertEqual(discovery._windows_app_paths(entry, registry), [])

    def test_explicit_absence_and_node_do_not_query_the_system(self):
        with patch.object(discovery, "_mac_app_paths") as mac, \
             patch.object(discovery, "_windows_app_paths") as windows:
            for platform in ("darwin", "win32", "linux"):
                with patch.object(discovery.sys, "platform", platform):
                    self.assertEqual(discovery._app_paths("claude"), [])
                    self.assertEqual(discovery._app_paths("node"), [])
            mac.assert_not_called()
            windows.assert_not_called()

    def test_cli_candidates_precede_apps_and_first_source_keeps_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manual, manager, common, registry, caller, app = [root / part for part in
                                                            ("manual", "manager", "common", "registry", "caller", "app")]
            filename = HarnessDiscoveryTests.candidate_name("codex")
            with patch.object(discovery, "_home", return_value=root), \
                 patch.object(discovery, "_manager_records", return_value=([], [("nvm", manager)])), \
                 patch.object(discovery, "_common_dirs", return_value=[common]), \
                 patch.object(discovery, "_registry_paths", return_value=[str(registry)]), \
                 patch.object(discovery, "_app_paths", side_effect=lambda name:
                              [caller / filename, app / filename] if name == "codex" else []):
                snapshot = discovery.candidate_snapshot("codex", manual_path=str(manual / filename),
                                                        environment={"PATH": str(caller)})
            candidates = snapshot["candidates"]
            sources = list(dict.fromkeys(item["source"] for item in candidates))
            self.assertEqual(sources, ["manual", "nvm", "common", "registry", "path", "app"])
            self.assertEqual([item["source"] for item in candidates if item["path"] == str(caller / filename)], ["path"])
            self.assertEqual(sorted(sources, key=discovery._priority), sources)


class ProbeStopEvidenceTests(unittest.TestCase):
    def test_snapshot_failure_proves_no_probe_created(self):
        with patch.object(discovery, 'candidate_snapshot', side_effect=ValueError('fixture snapshot failed')), \
             patch.object(discovery, 'probe') as probe:
            with self.assertRaises(ValueError) as caught:
                discovery.discover('codex', environment={'PATH': ''}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
        probe.assert_not_called()
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)

    def test_candidate_preparation_failure_proves_no_probe_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'codex'
            path.write_text('plain fixture', encoding="utf-8", newline="\n")
            candidate = {'path': str(path), 'source': 'manual', 'fingerprint': {}, 'exists': True}
            with patch.object(discovery, '_launch_command', _REAL_LAUNCH_COMMAND), \
                 patch.object(discovery, '_script_launch', side_effect=ValueError('fixture input failed')), \
                 patch.object(discovery, 'probe') as probe:
                with self.assertRaises(ValueError) as caught:
                    discovery._candidate_handshake('codex', candidate, {}, time.monotonic() + 1, version_probe=discovery._probe_cli_version)
        probe.assert_not_called()
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)

    def test_unknown_probe_exception_never_implies_stop(self):
        candidate = {'path': '/fixture/codex', 'source': 'manual', 'fingerprint': {}, 'exists': True}
        with patch.object(discovery, '_launch_command', return_value=['/fixture/codex']), \
             patch.object(discovery, 'probe', side_effect=ValueError('fixture unknown process state')):
            with self.assertRaises(ValueError) as caught:
                discovery._candidate_handshake('codex', candidate, {}, time.monotonic() + 1, version_probe=discovery._probe_cli_version)
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), False)

    def test_post_probe_error_keeps_confirmed_stop(self):
        candidate = {'path': '/fixture/codex', 'source': 'manual', 'fingerprint': {}, 'exists': True}
        with patch.object(discovery, '_launch_command', return_value=['/fixture/codex']), \
             patch.object(discovery, 'probe', return_value=(None, b'codex 1.0')), \
             patch.object(discovery, '_VERSION') as version:
            version.search.side_effect = ValueError('fixture version parsing failed')
            with self.assertRaises(ValueError) as caught:
                discovery._candidate_handshake('codex', candidate, {}, time.monotonic() + 1, version_probe=discovery._probe_cli_version)
        self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), True)

    def test_scan_keeps_probe_exception_stop_evidence(self):
        candidate = {'path': '/fixture/codex', 'source': 'manual', 'fingerprint': {}, 'exists': True}
        for stopped in (True, False):
            with self.subTest(stopped=stopped):
                error = ValueError('fixture probe failed')
                error.discovery_shutdown_confirmed = stopped
                with patch.object(discovery, 'candidate_snapshot', return_value={'candidates': [candidate]}), \
                     patch.object(discovery, '_candidate_handshake', side_effect=error):
                    with self.assertRaises(ValueError) as caught:
                        discovery.discover('codex', environment={'PATH': ''}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
                self.assertIs(getattr(caught.exception, "discovery_shutdown_confirmed", None), stopped)

    def test_login_text_cannot_hide_unverified_shutdown(self):
        candidate = {'path': '/fixture/cli', 'source': 'manual', 'fingerprint': {}, 'exists': True}
        for harness, output in (('codex', b'Not logged in'), ('claude', b'{"loggedIn":false}')):
            with self.subTest(harness=harness), \
                 patch.object(discovery, '_launch_command', return_value=['/fixture/cli']), \
                 patch.object(discovery, 'probe', side_effect=[(None, b'fixture 1.0'), ('shutdown-unverified', output)]):
                result = discovery._candidate_handshake(harness, candidate, {}, time.monotonic() + 1, version_probe=discovery._probe_cli_version)
            self.assertEqual(result['reason']['code'], 'harness.shutdown-unverified')

    def test_adjacent_node_unverified_stop_cannot_fall_through(self):
        with patch.object(discovery, '_variants', return_value=[Path('/fixture/node')]), \
             patch.object(discovery, 'file_fingerprint', return_value={}), \
             patch.object(discovery, '_native_command', return_value=['/fixture/node']), \
             patch.object(discovery, 'probe', return_value=('shutdown-unverified', b'')), \
             patch.object(discovery, '_discover') as scan:
            node, reason = discovery._node_for(Path('/fixture/cli'), {}, time.monotonic() + 1)
        self.assertIsNone(node)
        self.assertEqual(reason, 'shutdown-unverified')
        scan.assert_not_called()

    def test_node_scan_unverified_stop_overrides_previous_failure(self):
        with patch.object(discovery, '_variants', return_value=[Path('/fixture/node')]), \
             patch.object(discovery, 'file_fingerprint', return_value={}), \
             patch.object(discovery, '_native_command', return_value=['/fixture/node']), \
             patch.object(discovery, 'probe', return_value=('probe-failed', b'')), \
             patch.object(discovery, '_discover', return_value={'available': False, 'status': 'unhealthy',
                                                              'reason': {'code': 'harness.shutdown-unverified', 'params': {'operation': 'probe'}}}):
            node, reason = discovery._node_for(Path('/fixture/cli'), {}, time.monotonic() + 1)
        self.assertIsNone(node)
        self.assertEqual(reason, 'shutdown-unverified')

    def probe_exception(self, stopped, *, eof=True, scan=False):
        with tempfile.TemporaryDirectory() as directory:
            cli = HarnessDiscoveryTests().plain_file(Path(directory) / 'cli', 'import time\ntime.sleep(20)\n')
            selector = discovery.selectors.DefaultSelector()
            select = selector.select
            first = True
            def failed_once(timeout):
                nonlocal first
                if first:
                    first = False
                    raise ValueError('fixture reader failed')
                return select(timeout) if eof else []
            terminate = discovery._terminate_group
            def actual_stop(process):
                result = terminate(process)
                self.assertTrue(result)
                return stopped
            with patch.object(discovery, '_launch_command',
                              side_effect=lambda path, _environment, **_kwargs: [sys.executable, str(path)]), \
                 patch.object(discovery.selectors, 'DefaultSelector', return_value=selector), \
                 patch.object(selector, 'select', side_effect=failed_once), \
                 patch.object(discovery, '_terminate_group', side_effect=actual_stop):
                with self.assertRaises(ValueError) as caught:
                    if scan:
                        with patch.object(discovery, '_home', return_value=Path(directory)), \
                             patch.object(discovery, '_common_dirs', return_value=[]), \
                             patch.object(discovery, '_app_paths', return_value=[]):
                            discovery.discover('zcode', manual_path=str(cli), environment={'PATH': ''}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
                    else:
                        discovery.probe([sys.executable, str(cli)], dict(os.environ), deadline=time.monotonic() + 1)
            return caught.exception

    @unittest.skipIf(os.name == 'nt', 'POSIX private probe')
    def test_actual_probe_exception_keeps_confirmed_stop(self):
        self.assertIs(getattr(self.probe_exception(True), "discovery_shutdown_confirmed", None), True)

    @unittest.skipIf(os.name == 'nt', 'POSIX private probe')
    def test_actual_probe_exception_keeps_unconfirmed_stop(self):
        self.assertIs(getattr(self.probe_exception(False), "discovery_shutdown_confirmed", None), False)

    @unittest.skipIf(os.name == 'nt', 'POSIX private probe')
    def test_normal_exit_without_group_stop_evidence_is_unverified(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = HarnessDiscoveryTests().plain_file(Path(directory) / 'cli', 'print("fixture 1.0")\n')
            terminate = discovery._terminate_group
            def unobserved(process):
                self.assertTrue(terminate(process))
                return False
            with patch.object(discovery, '_terminate_group', side_effect=unobserved):
                code, output = discovery.probe([sys.executable, str(cli)], dict(os.environ), deadline=time.monotonic() + 1)
        self.assertEqual((code, output), ('shutdown-unverified', b''))

    def test_node_probe_exception_keeps_actual_stop(self):
        for stopped in (True, False):
            with self.subTest(stopped=stopped):
                error = ValueError('fixture node probe failed')
                error.discovery_shutdown_confirmed = stopped
                with patch.object(discovery, '_variants', return_value=[Path('/fixture/node')]), \
                     patch.object(discovery, 'file_fingerprint', return_value={}), \
                     patch.object(discovery, '_native_command', return_value=['/fixture/node']), \
                     patch.object(discovery, 'probe', side_effect=error):
                    with self.assertRaises(ValueError) as caught:
                        discovery._node_for(Path('/fixture/cli'), {}, time.monotonic() + 1)
                self.assertIs(getattr(caught.exception, 'discovery_shutdown_confirmed', None), stopped)

    def test_dsh_probe_exception_keeps_actual_stop(self):
        from hey_my_buddy.buddy.harnesses.base import ProcessHandle
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cli = HarnessDiscoveryTests().plain_file(root / 'dsh', 'print("dsh 1.0")\n')
            for stopped in (True, False):
                with self.subTest(stopped=stopped):
                    private = root / ('probe-' + str(stopped))
                    private.mkdir()
                    observe = ProcessHandle.shutdown_confirmed
                    def actual_stop(handle, **kwargs):
                        actual = observe(handle, **kwargs)
                        return actual and stopped
                    with patch.object(discovery.private_dirs, 'make_temporary_dir', return_value=private), \
                         patch.object(discovery, '_probe_windows' if os.name == 'nt' else '_probe_posix',
                                      side_effect=ValueError('fixture reader failed')), \
                         patch.object(ProcessHandle, 'shutdown_confirmed', actual_stop):
                        with self.assertRaises(ValueError) as caught:
                            discovery._probe_dsh_version([sys.executable, str(cli)], {'PATH': ''}, time.monotonic() + 1, state_dir=Path(os.environ["HMB_STATE_DIR"]))
                    self.assertIs(getattr(caught.exception, 'discovery_shutdown_confirmed', None), stopped)

    @unittest.skipIf(os.name == 'nt', 'POSIX private probe')
    def test_probe_exception_without_eof_keeps_unconfirmed_stop(self):
        self.assertIs(getattr(self.probe_exception(True, eof=False), 'discovery_shutdown_confirmed', None), False)

    @unittest.skipIf(os.name == 'nt', 'Windows selects a fixed adjacent node.exe without _variants')
    def test_node_early_failure_proves_no_probe_created(self):
        with patch.object(discovery, '_variants', side_effect=ValueError('fixture node input failed')):
            with self.assertRaises(ValueError) as caught:
                discovery._node_for(Path('/fixture/cli'), {}, time.monotonic() + 1)
        self.assertIs(getattr(caught.exception, 'discovery_shutdown_confirmed', None), True)

    def test_scan_unknown_candidate_failure_keeps_unconfirmed_stop(self):
        candidate = {'path': '/fixture/codex', 'source': 'manual', 'fingerprint': {}, 'exists': True}
        with patch.object(discovery, 'candidate_snapshot', return_value={'candidates': [candidate]}), \
             patch.object(discovery, '_candidate_handshake', side_effect=ValueError('fixture unknown stop')):
            with self.assertRaises(ValueError) as caught:
                discovery.discover('codex', environment={'PATH': ''}, state_dir=Path(os.environ["HMB_STATE_DIR"]))
        self.assertIs(getattr(caught.exception, 'discovery_shutdown_confirmed', None), False)

    def test_node_unknown_probe_failure_keeps_unconfirmed_stop(self):
        with patch.object(discovery, '_variants', return_value=[Path('/fixture/node')]), \
             patch.object(discovery, 'file_fingerprint', return_value={}), \
             patch.object(discovery, '_native_command', return_value=['/fixture/node']), \
             patch.object(discovery, 'probe', side_effect=ValueError('fixture unknown stop')):
            with self.assertRaises(ValueError) as caught:
                discovery._node_for(Path('/fixture/cli'), {}, time.monotonic() + 1)
        self.assertIs(getattr(caught.exception, 'discovery_shutdown_confirmed', None), False)

    def test_node_environment_failure_proves_no_probe_created(self):
        with patch.object(discovery, '_variants', return_value=[Path('/fixture/node')]), \
             patch.object(discovery, 'file_fingerprint', return_value={}), \
             patch.object(discovery, '_native_command', return_value=['/fixture/node']), \
             patch.object(discovery, 'native_environment', side_effect=ValueError('fixture environment failed')), \
             patch.object(discovery, 'probe') as probe:
            with self.assertRaises(ValueError) as caught:
                discovery._node_for(Path('/fixture/cli'), {}, time.monotonic() + 1)
        probe.assert_not_called()
        self.assertIs(getattr(caught.exception, 'discovery_shutdown_confirmed', None), True)

    @unittest.skipIf(os.name == 'nt', 'POSIX private scan')
    def test_actual_scan_probe_exception_keeps_confirmed_stop(self):
        self.assertIs(getattr(self.probe_exception(True, scan=True), 'discovery_shutdown_confirmed', None), True)

    @unittest.skipIf(os.name == 'nt', 'POSIX private scan')
    def test_actual_scan_probe_exception_keeps_unconfirmed_stop(self):
        self.assertIs(getattr(self.probe_exception(False, scan=True), 'discovery_shutdown_confirmed', None), False)


if __name__ == "__main__":
    unittest.main()
