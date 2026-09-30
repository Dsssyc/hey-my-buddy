"""Private, model-free tests for the shared harness locator."""
from __future__ import annotations

from pathlib import Path
import os
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from buddy import harness_discovery as discovery


class HarnessDiscoveryTests(unittest.TestCase):
    @staticmethod
    def executable(path: Path, source: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!{sys.executable}\n" + source)
        path.chmod(0o755)
        return path

    def test_snapshot_only_fingerprints_candidates_and_manager_records(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bin_dir = home / "bin"
            bin_dir.mkdir()
            cli = bin_dir / "codex"
            cli.write_text("stub")
            record = home / ".nvm/alias/default"
            record.parent.mkdir(parents=True)
            record.write_text("v24.1.0")
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

    def test_manual_failure_falls_through_and_manager_version_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bad = self.executable(home / "bad/codex", "import sys\nprint(\"private-output\")\nsys.exit(3)\n")
            nvm = home / ".nvm/versions/node/v24.1.0/bin/codex"
            self.executable(nvm, "import sys\nprint(\"Logged in\" if sys.argv[1:] == [\"login\", \"status\"] else \"codex 1.2.0\")\n")
            fnm = home / ".fnm/node-versions/v25.0.0/installation/bin/codex"
            self.executable(fnm, "import sys\nprint(\"Logged in\" if sys.argv[1:] == [\"login\", \"status\"] else \"codex 2.0.0\")\n")
            for path, value in ((home / ".nvm/alias/default", "v24.1.0"),
                                (home / ".fnm/aliases/default", "v25.0.0")):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value)
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                result = discovery.discover("codex", manual_path=str(bad), environment={"PATH": ""})
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
            script.write_text("// npm target")
            launcher = bin_dir / "codex"
            launcher.symlink_to(script)
            node = self.executable(bin_dir / "node", "import sys\nprint(\"codex 3.0.0\" if len(sys.argv) > 2 else \"v24.1.0\")\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]), patch.object(
                discovery, "_probe", wraps=discovery._probe
            ) as probe:
                result = discovery.discover("zcode", manual_path=str(launcher), environment={"PATH": ""})
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["command"], [str(node), str(launcher)])
            self.assertTrue(any(call.args[0][:1] == [str(node)] for call in probe.call_args_list))

    def test_failed_login_and_overflow_keep_output_private(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cli = self.executable(home / "codex", "import sys\nprint(\"codex 1.0.0\" if sys.argv[1:] == [\"--version\"] else \"Not logged in private-account\")\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                result = discovery.discover("codex", manual_path=str(cli), environment={"PATH": ""})
            self.assertEqual(result["status"], "login-required")
            self.assertFalse(result["available"])
            self.assertNotIn("private-account", str(result))
            self.executable(cli, "print(\"secret\" * 10000)\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                result = discovery.discover("codex", manual_path=str(cli), environment={"PATH": ""})
            self.assertEqual(result["reasonCode"], "output-limit")
            self.assertNotIn("secret", str(result))

    def test_snapshot_hash_changes_when_default_record_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            record = home / ".nvm/alias/default"
            record.parent.mkdir(parents=True)
            record.write_text("v22.0.0")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                first = discovery.candidate_snapshot("node", environment={"PATH": ""})
                record.write_text("v24.0.0")
                second = discovery.candidate_snapshot("node", environment={"PATH": ""})
            self.assertNotEqual(discovery._scan_hash(first), discovery._scan_hash(second))

    def test_cli_scan_changes_when_adjacent_node_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cli = home / "bin/codex"
            node = home / "bin/node"
            cli.parent.mkdir()
            cli.write_text("#!/usr/bin/env node\n")
            node.write_text("node-one")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                first = discovery.candidate_snapshot("codex", manual_path=str(cli), environment={"PATH": ""})
                node.write_text("node-two-with-new-size")
                second = discovery.candidate_snapshot("codex", manual_path=str(cli), environment={"PATH": ""})
            self.assertNotEqual(discovery._scan_hash(first), discovery._scan_hash(second))

    def test_claude_login_check_and_dsh_missing_without_native_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            claude = self.executable(home / "claude", "import sys\nprint(\"claude 2.1.0\" if sys.argv[1:] == [\"--version\"] else \'{\"loggedIn\":true,\"apiProvider\":\"anthropic\"}\')\n")
            node = self.executable(home / "bin/node", "import sys\nprint(\"v24.2.0\" if sys.argv[1:] == [\"--version\"] else \"checked\")\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[node.parent]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                claude_result = discovery.discover("claude", manual_path=str(claude), environment={"PATH": ""})
                dsh_result = discovery.discover("dsh", environment={"PATH": ""})
                node_result = discovery.discover("node", manual_path=str(node), environment={"PATH": ""})
            self.assertEqual(claude_result["status"], "ready")
            self.assertEqual(dsh_result["status"], "missing")
            self.assertFalse(dsh_result["available"])
            self.assertEqual(node_result["status"], "ready")

    def test_native_dsh_cli_requires_its_own_version_handshake(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            dsh = self.executable(home / "dsh", 'print("dsh 1.2.0")\n')
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                result = discovery.discover("dsh", manual_path=str(dsh), environment={"PATH": ""})
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["executable"], str(dsh))

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
                path.write_text(value)
            locations = [
                home / ".nvm/versions/node/v24.2.1/bin/codex",
                home / ".nvm/versions/node/v24.2.10/bin/codex",
                home / ".fnm/node-versions/v24.3.0/installation/bin/codex",
                home / ".asdf/installs/nodejs/24.4.0/bin/codex",
                home / ".local/share/mise/installs/node/24.5.0/bin/codex",
            ]
            for location in locations:
                location.parent.mkdir(parents=True, exist_ok=True)
                location.write_text("stub")
            direct_node = home / ".fnm/node-versions/v24.3.0/installation/node"
            direct_node.write_text("stub")
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
            (home / ".nvm/alias/default").write_text("node")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                generic = discovery.candidate_snapshot("codex", environment={"PATH": ""})
            self.assertIn(str(locations[1]), [item["path"] for item in generic["candidates"]])

    def test_manual_ready_skips_lower_priority_and_unknown_version_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cli = self.executable(home / "manual/zcode", 'print("release next: private detail")\n')
            marker = home / "lower-ran"
            lower = self.executable(home / "common/zcode", f'from pathlib import Path\nPath({str(marker)!r}).write_text("ran")\nprint("v9.0.0")\n')
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[lower.parent]
            ), patch.object(discovery, "_app_paths", return_value=[]):
                result = discovery.discover("zcode", manual_path=str(cli), environment={"PATH": ""})
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["version"], "unknown")
            self.assertEqual(result["reasonCode"], "version-unknown")
            self.assertFalse(marker.exists())
            self.assertNotIn("private detail", str(result))

    @unittest.skipIf(os.name == "nt", "POSIX process-group assertion")
    def test_probe_timeout_reaps_child_group_and_scan_has_total_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            child = 'import time; time.sleep(10)'
            cli = self.executable(home / "zcode", f'import subprocess, sys, time\nsubprocess.Popen([sys.executable, "-c", {child!r}], stdout=sys.stdout)\ntime.sleep(10)\n')
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[]
            ), patch.object(discovery, "_app_paths", return_value=[]), patch.object(
                discovery, "_SCAN_TIMEOUT", 0.4
            ), patch.object(discovery.os, "killpg", wraps=os.killpg) as kill_group:
                started = time.monotonic()
                result = discovery.discover("zcode", manual_path=str(cli), environment={"PATH": ""})
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(result["status"], "unhealthy")
            # A slow OS reaper may not close the inherited pipe within the
            # cleanup budget. It must remain unavailable with honest evidence.
            self.assertIn(result["reasonCode"], ("scan-timeout", "shutdown-unverified"))
            self.assertIn(signal.SIGKILL, [call.args[1] for call in kill_group.call_args_list])

    @unittest.skipIf(os.name == "nt", "POSIX process-group assertion")
    def test_total_deadline_covers_multiple_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            manual = self.executable(home / "manual/zcode", "import time\ntime.sleep(10)\n")
            lower = self.executable(home / "common/zcode", "import time\ntime.sleep(10)\n")
            with patch.object(discovery, "_home", return_value=home), patch.object(
                discovery, "_common_dirs", return_value=[lower.parent]
            ), patch.object(discovery, "_app_paths", return_value=[]), patch.object(
                discovery, "_TIMEOUT", 0.25
            ), patch.object(discovery, "_SCAN_TIMEOUT", 0.45):
                started = time.monotonic()
                result = discovery.discover("zcode", manual_path=str(manual), environment={"PATH": ""})
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertEqual(result["reasonCode"], "scan-timeout")
            self.assertEqual(len(result["candidates"]), 2)

    def test_native_environment_does_not_enumerate_values(self):
        class NoEnumeration(dict):
            def items(self):
                raise AssertionError("environment enumeration forbidden")

        environment = NoEnumeration({"PATH": "/usr/bin", "HOME": "/home/user",
                                     "LC_CTYPE": "en_US.UTF-8", "LC_SECRET_TOKEN": "private",
                                     "OPENAI_API_KEY": "private", "BUDDY_AGENT_CREDENTIAL": "private",
                                     "ANTHROPIC_BASE_URL": "third-party"})
        result = discovery.native_environment(environment)
        self.assertEqual(result["LC_CTYPE"], "en_US.UTF-8")
        self.assertNotIn("LC_SECRET_TOKEN", result)
        self.assertNotIn("OPENAI_API_KEY", result)
        self.assertNotIn("ANTHROPIC_BASE_URL", result)
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "codex"
            cli.write_text("stub")
            path = discovery.native_environment({"PATH": ""}, command=[str(cli)])["PATH"]
            self.assertEqual(path, str(cli.parent))

    def test_powershell_launch_never_loads_profile(self):
        with patch.object(discovery.shutil, "which", return_value="C:/pwsh.exe"):
            command = discovery._launch_command(Path("C:/tools/claude.ps1"), {"PATH": "C:/tools"})
        self.assertEqual(command, ["C:/pwsh.exe", "-NoProfile", "-NonInteractive", "-File",
                                   "C:/tools/claude.ps1"])

    def test_native_environment_is_allowlisted_and_command_prepends_bins(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "codex"
            cli.write_text("stub")
            result = discovery.native_environment({"PATH": "/usr/bin", "HOME": "/home/user",
                                                   "HTTPS_PROXY": "proxy", "OPENAI_API_KEY": "secret",
                                                   "ANTHROPIC_BASE_URL": "third-party",
                                                   "BUDDY_AGENT_CREDENTIAL": "internal"}, command=[str(cli)])
            self.assertEqual(result["PATH"].split(":")[0], str(cli.parent))
            self.assertEqual(result["HOME"], "/home/user")
            self.assertNotIn("OPENAI_API_KEY", result)
            self.assertNotIn("ANTHROPIC_BASE_URL", result)
            self.assertNotIn("BUDDY_AGENT_CREDENTIAL", result)

    def test_native_environment_keeps_the_login_name_for_keychain_logins(self):
        result = discovery.native_environment({"PATH": "/usr/bin", "HOME": "/home/user", "USER": "user",
                                               "LOGNAME": "user", "USERNAME": "user",
                                               "ANTHROPIC_API_KEY": "secret"})
        self.assertEqual((result["USER"], result["LOGNAME"], result["USERNAME"]), ("user", "user", "user"))
        self.assertNotIn("ANTHROPIC_API_KEY", result)

    def test_native_environment_keeps_ca_certificate_paths(self):
        # Certificate paths name trust roots, not credentials; everything else
        # outside the allowlist (provider keys, Buddy state) stays filtered.
        certificates = {"SSL_CERT_FILE": "/certs/root.pem", "SSL_CERT_DIR": "/certs/dirs",
                        "REQUESTS_CA_BUNDLE": "/certs/bundle.pem", "CURL_CA_BUNDLE": "/certs/curl.pem",
                        "NODE_EXTRA_CA_CERTS": "/certs/node.pem"}
        result = discovery.native_environment({"PATH": "/usr/bin", "HOME": "/home/user",
                                               "OPENAI_API_KEY": "secret", "BUDDY_STATE_DIR": "/private/state",
                                               **certificates})
        self.assertEqual({key: result.get(key) for key in certificates}, certificates)
        self.assertNotIn("OPENAI_API_KEY", result)
        self.assertNotIn("BUDDY_STATE_DIR", result)


if __name__ == "__main__":
    unittest.main()
