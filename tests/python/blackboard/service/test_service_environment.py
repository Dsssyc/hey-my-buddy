"""ADR-018 item 21: every service process starts from an explicit environment allowlist.

The launcher, the cold-start path and the upgrade path are checked directly.
The CLI invocation path keeps a scoped attempt credential.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.install import launcher, upgrade
from hey_my_buddy.protocol import transport


#: Variables a Claude Code or Codex Host session exports that must never enter a
#: long-lived service process, plus a representative attempt credential.
HOST_SESSION_VARIABLES = {
    "CLAUDECODE": "1",
    "HMB_DEBUG": "1",
    "CLAUDE_CODE_ENTRYPOINT": "cli",
    "CLAUDE_CODE_SESSION_ID": "9f1c4a2e-0000-4000-8000-000000000001",
    "CLAUDE_CODE_SSE_PORT": "12345",
    "CLAUDE_CODE_OAUTH_TOKEN": "session-token",
    "CLAUDE_CODE_EFFORT_LEVEL": "max",
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "ANTHROPIC_API_KEY": "provider-key",
    "ANTHROPIC_AUTH_TOKEN": "bearer-token",
    "ANTHROPIC_BASE_URL": "https://gateway.example.invalid",
    "ANTHROPIC_MODEL": "claude-other",
    "OPENAI_BASE_URL": "https://gateway.example.invalid/v1",
    "CODEX_SESSION_ID": "codex-session",
    "CODEX_API_KEY": "codex-key",
    "HMB_AGENT_CREDENTIAL_FILE": "/private/attempt.json",
    "HMB_TASK_ID": "run:host-session",
    "HMB_ATTEMPT_ID": "attempt:host-session",
    "HMB_HARNESS_RECORD_FILE": "/private/harness-selection.json",
}

#: Values a service must keep: process basics, native account directories and the
#: program's own private roots and explicitly supported settings.
SERVICE_KEPT_VARIABLES = {
    "HOME": "/users/fixture",
    "PATH": "/usr/bin:/bin",
    "USER": "fixture",
    "LOGNAME": "fixture",
    "TMPDIR": "/tmp/fixture",
    "SystemRoot": "C:\\Windows",
    "SystemDrive": "C:",
    "CLAUDE_CONFIG_DIR": "/users/fixture/.claude",
    "CODEX_HOME": "/users/fixture/.codex",
    "ZCODE_DATA_BASE_DIR": "/users/fixture/.zcode",
    "DSH_HOME": "/users/fixture/.dsh",
    "HMB_STATE_DIR": "/private/service/state",
    "HMB_RUNTIME_ROOT": "/private/service/runtime",
    "HMB_MAX_CONCURRENT": "4",
    "HMB_WAIT_CAPACITY": "16",
    "HMB_CONSOLE_PORT": "0",
    "HMB_MODEL_CATALOG_FILE": "/private/catalog.json",
}


def forbidden_environment(environment: dict, *, ignore: frozenset[str] = frozenset()) -> set[str]:
    return (set(HOST_SESSION_VARIABLES) - ignore) & set(environment)


class ServiceEnvironmentBuilderTests(unittest.TestCase):
    def test_upgrade_uses_its_selected_runtime_even_from_a_development_worker(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-upgrade-env-") as directory:
            with mock.patch.dict(os.environ, {"HMB_DEV_SOURCE": "1",
                                              "HMB_RUNTIME": "/old"}, clear=True):
                with mock.patch.object(upgrade.runtime, "read_ready", return_value={"sourceCommit": "f" * 40}):
                    environment = upgrade._environment(Path(directory), Path(directory) / "selected")
            self.assertNotIn("HMB_DEV_SOURCE", environment)
            self.assertEqual(environment["HMB_RUNTIME"], str(Path(directory) / "selected"))

    def test_the_builder_is_an_allowlist_not_the_host_session(self):
        with mock.patch.dict(os.environ, {**HOST_SESSION_VARIABLES, **SERVICE_KEPT_VARIABLES}, clear=True):
            environment = launcher.service_environment()
        self.assertEqual(forbidden_environment(environment), set())
        self.assertEqual(environment["HOME"], "/users/fixture")
        self.assertEqual(environment["TMPDIR"], "/tmp/fixture")
        self.assertEqual(environment["SystemRoot"], "C:\\Windows")
        self.assertEqual(environment["CLAUDE_CONFIG_DIR"], "/users/fixture/.claude")
        self.assertEqual(environment["HMB_STATE_DIR"], "/private/service/state")
        self.assertEqual(environment["HMB_RUNTIME_ROOT"], "/private/service/runtime")
        self.assertEqual(environment["HMB_MAX_CONCURRENT"], "4")
        # The development extension is read only under the explicit switch.
        self.assertNotIn("HMB_DEV_SOURCE", environment)
        self.assertNotIn("HMB_CLAUDE_CLI", environment)

    def test_ca_certificate_paths_reach_the_service_environment(self):
        # Certificate paths name trust roots, not credentials: the service and
        # its native children need them for TLS while Host secrets stay behind.
        certificates = {"SSL_CERT_FILE": "/certs/root.pem", "SSL_CERT_DIR": "/certs/dirs",
                        "REQUESTS_CA_BUNDLE": "/certs/bundle.pem", "CURL_CA_BUNDLE": "/certs/curl.pem",
                        "NODE_EXTRA_CA_CERTS": "/certs/node.pem"}
        with mock.patch.dict(os.environ, {**HOST_SESSION_VARIABLES, **certificates}, clear=True):
            environment = launcher.service_environment()
        self.assertEqual({key: environment.get(key) for key in certificates}, certificates)
        self.assertEqual(forbidden_environment(environment), set())

    def test_the_development_extension_names_each_fixture_instead_of_a_prefix_rule(self):
        inherited = {**HOST_SESSION_VARIABLES,
                     "HMB_DEV_SOURCE": "1",
                     "HMB_CLAUDE_CLI": "/private/fixtures/claude",
                     "HMB_CODEX_CLI": "/private/fixtures/codex",
                     "HMB_CLAUDE_FIXTURE_CASE": "quota-rejected",
                     "HMB_CODEX_FIXTURE_STATE": "/private/fixture.json",
                     "HMB_NOT_A_REAL_SETTING": "must-not-pass",
                     "HMB_AGENT_CREDENTIAL_FILE": "/private/attempt.json"}
        with mock.patch.dict(os.environ, inherited, clear=True):
            environment = launcher.service_environment()
        self.assertEqual(environment["HMB_DEV_SOURCE"], "1")
        self.assertEqual(environment["HMB_CLAUDE_CLI"], "/private/fixtures/claude")
        self.assertEqual(environment["HMB_CODEX_CLI"], "/private/fixtures/codex")
        self.assertEqual(environment["HMB_CLAUDE_FIXTURE_CASE"], "quota-rejected")
        self.assertEqual(environment["HMB_CODEX_FIXTURE_STATE"], "/private/fixture.json")
        self.assertNotIn("HMB_NOT_A_REAL_SETTING", environment)
        self.assertEqual(forbidden_environment(environment), set())

    def test_explicit_overrides_win_over_ambient_values(self):
        with mock.patch.dict(os.environ, {"HMB_STATE_DIR": "/ambient", "PATH": "/usr/bin"}, clear=True):
            environment = launcher.service_environment({"HMB_STATE_DIR": "/explicit"})
        self.assertEqual(environment["HMB_STATE_DIR"], "/explicit")
        self.assertEqual(environment["PATH"], "/usr/bin")

    def test_the_cli_invocation_path_preserves_attempt_credential(self):
        with mock.patch.dict(os.environ, {"HMB_AGENT_CREDENTIAL_FILE": "/private/attempt.json"}, clear=True):
            environment = launcher.runtime_environment(Path("/private/runtime") / ("a" * 32))
        self.assertEqual(environment["HMB_AGENT_CREDENTIAL_FILE"], "/private/attempt.json")


class ColdStartEnvironmentTests(unittest.TestCase):
    def test_cold_start_builds_the_daemon_environment_from_the_allowlist(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-service-env-") as temporary:
            state = Path(temporary) / "state"
            target = {"python": "/stable/venv/bin/python", "pythonPath": None, "identity": "runtime:fixed",
                      "stable": True, "installed": False,
                      "runtime": {"runtimeDir": "/stable/runtime", "environment": "/stable/venv"}}
            with mock.patch.dict(os.environ, {**HOST_SESSION_VARIABLES, **SERVICE_KEPT_VARIABLES}, clear=True), \
                    mock.patch.object(transport, "_attach_read_only", return_value=None), \
                    mock.patch("hey_my_buddy.install.runtime.launch_target", return_value=target), \
                    mock.patch.object(transport, "_healthy", return_value={"pid": 4242}), \
                    mock.patch("hey_my_buddy.install.launcher.write_active_runtime"), \
                    mock.patch.object(transport.subprocess, "Popen") as spawn:
                spawn.return_value.poll.return_value = None
                endpoint = transport.ensure_service(state)
            self.assertEqual(endpoint, {"pid": 4242})
            environment = spawn.call_args.kwargs["env"]
            self.assertEqual(forbidden_environment(environment), set())
            self.assertEqual(environment["HMB_STATE_DIR"], str(state.resolve()))
            self.assertEqual(environment["HMB_RUNTIME"], "/stable/runtime")
            self.assertEqual(environment["HMB_RUNTIME_IDENTITY"], "runtime:fixed")
            self.assertEqual(environment["CLAUDE_CONFIG_DIR"], "/users/fixture/.claude")
            self.assertEqual(environment["C2_RELAY_ANCHOR_ADDRESS"], "")
            self.assertEqual(environment["C2_ENV_FILE"], "")

    def test_cold_start_keeps_the_development_checkout_through_python_path_only(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-service-dev-") as temporary:
            state = Path(temporary) / "state"
            target = {"python": sys.executable, "pythonPath": "/checkout/src", "identity": "source:fixture",
                      "stable": False, "installed": False, "runtime": {"runtimeDir": "/unused"}}
            with mock.patch.dict(os.environ, {**HOST_SESSION_VARIABLES, "HMB_DEV_SOURCE": "1",
                                              "HMB_CLAUDE_CLI": "/private/fixtures/claude",
                                              "PYTHONPATH": "/host/injected"}, clear=True), \
                    mock.patch.object(transport, "_attach_read_only", return_value=None), \
                    mock.patch("hey_my_buddy.install.runtime.launch_target", return_value=target), \
                    mock.patch.object(transport, "_healthy", return_value={"pid": 4242}), \
                    mock.patch.object(transport.subprocess, "Popen") as spawn:
                spawn.return_value.poll.return_value = None
                transport.ensure_service(state)
            environment = spawn.call_args.kwargs["env"]
            self.assertEqual(forbidden_environment(environment), set())
            self.assertEqual(environment["HMB_DEV_SOURCE"], "1")
            self.assertEqual(environment["HMB_CLAUDE_CLI"], "/private/fixtures/claude")
            self.assertEqual(environment["PYTHONPATH"], "/checkout/src")
            self.assertNotIn("HMB_RUNTIME", environment)


class UpgradeEnvironmentTests(unittest.TestCase):
    def test_upgrade_start_uses_the_allowlist_and_the_journaled_launch_settings(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-service-upgrade-") as temporary:
            state = Path(temporary)
            launcher.write_private(state / "upgrade.json",
                                   {"environment": {"HMB_MAX_CONCURRENT": "7", "HMB_WAIT_CAPACITY": "32"}})
            target = state / "target"
            with mock.patch.dict(os.environ, {**HOST_SESSION_VARIABLES, **SERVICE_KEPT_VARIABLES}, clear=True):
                with mock.patch.object(upgrade.runtime, "read_ready", return_value={"sourceCommit": "f" * 40}):
                    environment = upgrade._environment(state, target)
        self.assertEqual(forbidden_environment(environment), set())
        self.assertEqual(environment["HMB_STATE_DIR"], str(state))
        self.assertEqual(environment["HMB_RUNTIME"], str(target))
        self.assertEqual(environment["HMB_RUNTIME_IDENTITY"], "runtime:" + target.name)
        self.assertEqual(environment["HMB_MAX_CONCURRENT"], "7")
        self.assertEqual(environment["HMB_WAIT_CAPACITY"], "32")
        self.assertNotIn("HMB_PYTHON", environment)
        self.assertEqual(environment["PATH"], "/usr/bin:/bin")


if __name__ == "__main__":
    unittest.main()
