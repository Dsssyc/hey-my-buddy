"""ADR-018 item 21: every service process starts from an explicit environment allowlist.

The launcher, the cold-start path, the upgrade path, the daemon's supervisor pool and
explicit worker start are checked directly. The CLI invocation path is the deliberate
exception: it must keep a scoped Worker credential, because dropping it would turn a
Worker into a service-token Host.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from buddy import cli, daemon, launcher, transport, upgrade


#: Variables a Claude Code or Codex Host session exports that must never enter a
#: long-lived service process, plus a representative attempt credential.
HOST_SESSION_VARIABLES = {
    "CLAUDECODE": "1",
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
    "BUDDY_AGENT_CREDENTIAL": "attempt-token",
    "BUDDY_AGENT_CREDENTIAL_FILE": "/private/attempt.json",
    "BUDDY_TASK_ID": "run:host-session",
    "BUDDY_ATTEMPT_ID": "attempt:host-session",
    "BUDDY_HARNESS_RECORD_FILE": "/private/harness-selection.json",
    "BUDDY_SUPERVISOR_START_ID": "host-session-start",
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
    "BUDDY_STATE_DIR": "/private/service/state",
    "BUDDY_RUNTIME_ROOT": "/private/service/runtime",
    "BUDDY_MAX_CONCURRENT": "4",
    "BUDDY_WAIT_CAPACITY": "16",
    "BUDDY_CONSOLE_PORT": "0",
    "BUDDY_MODEL_CATALOG_FILE": "/private/catalog.json",
}


def forbidden_environment(environment: dict, *, ignore: frozenset[str] = frozenset()) -> set[str]:
    return (set(HOST_SESSION_VARIABLES) - ignore) & set(environment)


class ServiceEnvironmentBuilderTests(unittest.TestCase):
    def test_the_builder_is_an_allowlist_not_the_host_session(self):
        with mock.patch.dict(os.environ, {**HOST_SESSION_VARIABLES, **SERVICE_KEPT_VARIABLES}, clear=True):
            environment = launcher.service_environment()
        self.assertEqual(forbidden_environment(environment), set())
        self.assertEqual(environment["HOME"], "/users/fixture")
        self.assertEqual(environment["TMPDIR"], "/tmp/fixture")
        self.assertEqual(environment["SystemRoot"], "C:\\Windows")
        self.assertEqual(environment["CLAUDE_CONFIG_DIR"], "/users/fixture/.claude")
        self.assertEqual(environment["BUDDY_STATE_DIR"], "/private/service/state")
        self.assertEqual(environment["BUDDY_RUNTIME_ROOT"], "/private/service/runtime")
        self.assertEqual(environment["BUDDY_MAX_CONCURRENT"], "4")
        # The development extension is read only under the explicit switch.
        self.assertNotIn("BUDDY_DEV_SOURCE", environment)
        self.assertNotIn("BUDDY_CLAUDE_CLI", environment)

    def test_the_development_extension_names_each_fixture_instead_of_a_prefix_rule(self):
        inherited = {**HOST_SESSION_VARIABLES,
                     "BUDDY_DEV_SOURCE": "1",
                     "BUDDY_CLAUDE_CLI": "/private/fixtures/claude",
                     "BUDDY_CODEX_CLI": "/private/fixtures/codex",
                     "BUDDY_CLAUDE_FIXTURE_CASE": "quota-rejected",
                     "BUDDY_CODEX_FIXTURE_STATE": "/private/fixture.json",
                     "BUDDY_NOT_A_REAL_SETTING": "must-not-pass",
                     "BUDDY_AGENT_CREDENTIAL_FILE": "/private/attempt.json"}
        with mock.patch.dict(os.environ, inherited, clear=True):
            environment = launcher.service_environment()
        self.assertEqual(environment["BUDDY_DEV_SOURCE"], "1")
        self.assertEqual(environment["BUDDY_CLAUDE_CLI"], "/private/fixtures/claude")
        self.assertEqual(environment["BUDDY_CODEX_CLI"], "/private/fixtures/codex")
        self.assertEqual(environment["BUDDY_CLAUDE_FIXTURE_CASE"], "quota-rejected")
        self.assertEqual(environment["BUDDY_CODEX_FIXTURE_STATE"], "/private/fixture.json")
        self.assertNotIn("BUDDY_NOT_A_REAL_SETTING", environment)
        self.assertEqual(forbidden_environment(environment), set())

    def test_explicit_overrides_win_over_ambient_values(self):
        with mock.patch.dict(os.environ, {"BUDDY_STATE_DIR": "/ambient", "PATH": "/usr/bin"}, clear=True):
            environment = launcher.service_environment({"BUDDY_STATE_DIR": "/explicit"})
        self.assertEqual(environment["BUDDY_STATE_DIR"], "/explicit")
        self.assertEqual(environment["PATH"], "/usr/bin")

    def test_the_cli_invocation_path_still_preserves_a_scoped_worker_credential(self):
        with mock.patch.dict(os.environ, {"BUDDY_AGENT_CREDENTIAL_FILE": "/private/attempt.json",
                                          "BUDDY_WORKER_ID": "local-2"}, clear=True):
            environment = launcher.runtime_environment(Path("/private/runtime") / ("a" * 32))
        self.assertEqual(environment["BUDDY_AGENT_CREDENTIAL_FILE"], "/private/attempt.json")
        self.assertEqual(environment["BUDDY_WORKER_ID"], "local-2")


class ColdStartEnvironmentTests(unittest.TestCase):
    def test_cold_start_builds_the_daemon_environment_from_the_allowlist(self):
        with tempfile.TemporaryDirectory(prefix="buddy-service-env-") as temporary:
            state = Path(temporary) / "state"
            target = {"python": "/stable/venv/bin/python", "pythonPath": None, "identity": "runtime:fixed",
                      "stable": True, "installed": False,
                      "runtime": {"runtimeDir": "/stable/runtime", "environment": "/stable/venv"}}
            with mock.patch.dict(os.environ, {**HOST_SESSION_VARIABLES, **SERVICE_KEPT_VARIABLES}, clear=True), \
                    mock.patch.object(transport, "_attach_read_only", return_value=None), \
                    mock.patch("buddy.runtime.launch_target", return_value=target), \
                    mock.patch.object(transport, "_healthy", return_value={"pid": 4242}), \
                    mock.patch("buddy.launcher.write_active_runtime"), \
                    mock.patch.object(transport.subprocess, "Popen") as spawn:
                spawn.return_value.poll.return_value = None
                endpoint = transport.ensure_service(state)
            self.assertEqual(endpoint, {"pid": 4242})
            environment = spawn.call_args.kwargs["env"]
            self.assertEqual(forbidden_environment(environment), set())
            self.assertEqual(environment["BUDDY_STATE_DIR"], str(state.resolve()))
            self.assertEqual(environment["BUDDY_RUNTIME"], "/stable/runtime")
            self.assertEqual(environment["BUDDY_RUNTIME_IDENTITY"], "runtime:fixed")
            self.assertEqual(environment["CLAUDE_CONFIG_DIR"], "/users/fixture/.claude")
            self.assertEqual(environment["C2_RELAY_ANCHOR_ADDRESS"], "")
            self.assertEqual(environment["C2_ENV_FILE"], "")

    def test_cold_start_keeps_the_development_checkout_through_python_path_only(self):
        with tempfile.TemporaryDirectory(prefix="buddy-service-dev-") as temporary:
            state = Path(temporary) / "state"
            target = {"python": sys.executable, "pythonPath": "/checkout/src", "identity": "source:fixture",
                      "stable": False, "installed": False, "runtime": {"runtimeDir": "/unused"}}
            with mock.patch.dict(os.environ, {**HOST_SESSION_VARIABLES, "BUDDY_DEV_SOURCE": "1",
                                              "BUDDY_CLAUDE_CLI": "/private/fixtures/claude",
                                              "PYTHONPATH": "/host/injected"}, clear=True), \
                    mock.patch.object(transport, "_attach_read_only", return_value=None), \
                    mock.patch("buddy.runtime.launch_target", return_value=target), \
                    mock.patch.object(transport, "_healthy", return_value={"pid": 4242}), \
                    mock.patch.object(transport.subprocess, "Popen") as spawn:
                spawn.return_value.poll.return_value = None
                transport.ensure_service(state)
            environment = spawn.call_args.kwargs["env"]
            self.assertEqual(forbidden_environment(environment), set())
            self.assertEqual(environment["BUDDY_DEV_SOURCE"], "1")
            self.assertEqual(environment["BUDDY_CLAUDE_CLI"], "/private/fixtures/claude")
            self.assertEqual(environment["PYTHONPATH"], "/checkout/src")
            self.assertNotIn("BUDDY_RUNTIME", environment)


class UpgradeEnvironmentTests(unittest.TestCase):
    def test_upgrade_start_uses_the_allowlist_and_the_journaled_launch_settings(self):
        with tempfile.TemporaryDirectory(prefix="buddy-service-upgrade-") as temporary:
            state = Path(temporary)
            launcher.write_private(state / "upgrade.json",
                                   {"environment": {"BUDDY_MAX_CONCURRENT": "7", "BUDDY_WAIT_CAPACITY": "32"}})
            target = state / "target"
            with mock.patch.dict(os.environ, {**HOST_SESSION_VARIABLES, **SERVICE_KEPT_VARIABLES}, clear=True):
                environment = upgrade._environment(state, target)
        self.assertEqual(forbidden_environment(environment), set())
        self.assertEqual(environment["BUDDY_STATE_DIR"], str(state))
        self.assertEqual(environment["BUDDY_RUNTIME"], str(target))
        self.assertEqual(environment["BUDDY_RUNTIME_IDENTITY"], "runtime:" + target.name)
        self.assertEqual(environment["BUDDY_MAX_CONCURRENT"], "7")
        self.assertEqual(environment["BUDDY_WAIT_CAPACITY"], "32")
        self.assertEqual(environment["BUDDY_PYTHON"], str(target / "venv/bin/python"))
        self.assertEqual(environment["PATH"], "/usr/bin:/bin")


class SupervisorEnvironmentTests(unittest.TestCase):
    def test_daemon_spawned_supervisor_gets_the_allowlist_and_a_fresh_start_id(self):
        with tempfile.TemporaryDirectory(prefix="buddy-service-supervisor-") as temporary:
            state = Path(temporary)
            target = {"python": sys.executable, "pythonPath": "/checkout/src", "identity": "source:fixture",
                      "stable": False, "installed": False, "runtime": {"runtimeDir": "/unused"}}
            handle = daemon.SupervisorHandle(state, "local")
            with mock.patch.dict(os.environ, {**HOST_SESSION_VARIABLES, "BUDDY_DEV_SOURCE": "1"}, clear=True), \
                    mock.patch.object(daemon.runtime, "launch_target", return_value=target), \
                    mock.patch.object(daemon.threading, "Thread"), \
                    mock.patch.object(daemon.subprocess, "Popen") as spawn:
                self.assertTrue(handle.start(state, state / "worker.log"))
            environment = spawn.call_args.kwargs["env"]
            self.assertEqual(forbidden_environment(environment, ignore=frozenset({"BUDDY_SUPERVISOR_START_ID"})), set())
            self.assertEqual(environment["BUDDY_WORKER_ID"], "local")
            self.assertEqual(environment["BUDDY_STATE_DIR"], str(state))
            self.assertEqual(environment["BUDDY_DEV_SOURCE"], "1")
            self.assertEqual(environment["PYTHONPATH"], "/checkout/src")
            self.assertNotIn("BUDDY_RUNTIME", environment)
            self.assertNotEqual(environment["BUDDY_SUPERVISOR_START_ID"], "host-session-start")
            self.assertTrue(environment["BUDDY_SUPERVISOR_START_ID"])


class ExplicitWorkerEnvironmentTests(unittest.TestCase):
    def test_explicit_worker_start_uses_the_allowlist_and_selects_its_own_runtime(self):
        with tempfile.TemporaryDirectory(prefix="buddy-service-worker-") as temporary:
            state = Path(temporary) / "state"
            target = {"python": "/stable/venv/bin/python", "pythonPath": None, "identity": "runtime:fixed",
                      "stable": True, "installed": False,
                      "runtime": {"runtimeDir": "/stable/runtime", "environment": "/stable/venv"}}
            with mock.patch.dict(os.environ, {**HOST_SESSION_VARIABLES, "PATH": "/usr/bin:/bin"}, clear=True), \
                    mock.patch.object(cli.runtime, "launch_target", return_value=target), \
                    mock.patch.object(cli.subprocess, "Popen") as spawn:
                spawn.return_value.pid = 123
                result = cli._worker_command("worker-start", {"workerId": "extra", "stateDir": str(state)})
            environment = spawn.call_args.kwargs["env"]
            self.assertEqual(result["supervisorPid"], 123)
            self.assertEqual(forbidden_environment(environment), set())
            self.assertEqual(environment["BUDDY_STATE_DIR"], str(state.resolve()))
            self.assertEqual(environment["BUDDY_WORKER_ID"], "extra")
            self.assertEqual(environment["BUDDY_RUNTIME"], "/stable/runtime")
            self.assertEqual(environment["BUDDY_RUNTIME_IDENTITY"], "runtime:fixed")
            self.assertEqual(environment["BUDDY_PYTHON"], "/stable/venv/bin/python")
            self.assertNotIn("PYTHONPATH", environment)
            self.assertNotIn("VIRTUAL_ENV", environment)
            self.assertEqual(environment["PATH"].split(os.pathsep)[0], "/stable/venv/bin")


if __name__ == "__main__":
    unittest.main()
