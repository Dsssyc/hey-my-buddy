"""Claude configuration policy unit tests; no real Claude Code CLI process is started."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.claude.config import (
    ClaudeUnavailable,
    DEFAULT_EFFORT,
    PACKAGE_REGISTRY_DOMAINS,
    READONLY_TOOLS,
    WRITABLE_TOOLS,
    cli_command,
    discovery_args,
    execution_args,
    native_environment,
    provider_from_account,
    sandbox_settings,
)
from hey_my_buddy.buddy.roles.turn_io import canonical_json


def flag_value(args: list[str], flag: str) -> str | None:
    for index, item in enumerate(args):
        if item == flag and index + 1 < len(args):
            return args[index + 1]
        if item.startswith(flag + "="):
            return item[len(flag) + 1:]
    return None


#: Inherited Worker/runtime pins a focused run must not depend on. ``hey_my_buddy.cli.checks`` sanitizes its children with its own list;
#: this file states the same boundary itself so `python -m unittest
#: tests.python.test_claude_config` is self-sufficient.
FOCUSED_CLEARED_VARIABLES = (
    "BUDDY_STATE_DIR", "BUDDY_RUNTIME_ROOT", "BUDDY_RUNTIME", "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE", "BUDDY_WORKER_ID", "BUDDY_AGENT_CREDENTIAL",
    "BUDDY_AGENT_CREDENTIAL_FILE", "BUDDY_HARNESS_RECORD_FILE",
    "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT",
)


class ClaudeConfigTests(unittest.TestCase):
    def test_native_provider_label_keeps_anthropic_identity_and_other_names(self):
        self.assertEqual(provider_from_account({"apiProvider": "firstParty"}), "anthropic")
        for provider in ("anthropic", "bedrock", "vertex", "foundry", "custom-gateway"):
            self.assertEqual(provider_from_account({"apiProvider": provider}), provider)
        for account in (None, {}, {"apiProvider": None}):
            self.assertEqual(provider_from_account(account), "unknown")

    def setUp(self):
        # The override cases below need the explicit development switch, and this
        # focused file must supply it rather than rely on hey_my_buddy.cli.checks having set it.
        environment = {key: value for key, value in os.environ.items()
                       if key not in FOCUSED_CLEARED_VARIABLES}
        environment["BUDDY_DEV_SOURCE"] = "1"
        patcher = mock.patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cli_override_and_missing_cli(self):
        with tempfile.TemporaryDirectory(prefix="buddy-claude-config-") as temp:
            cli = Path(temp) / "claude"
            cli.write_text("#!/bin/sh\n")
            cli.chmod(0o755)
            self.assertEqual(cli_command({"BUDDY_CLAUDE_CLI": str(cli), "PATH": tempfile.mkdtemp()}),
                             [str(cli.resolve())])
            with tempfile.TemporaryDirectory(prefix="buddy-claude-empty-") as empty:
                with self.assertRaises(ClaudeUnavailable):
                    cli_command({"PATH": empty})









    def test_sandbox_settings_use_the_documented_network_keys(self):
        self.assertEqual(set(sandbox_settings()), {"sandbox"})
        sandbox = sandbox_settings()["sandbox"]
        self.assertTrue(sandbox["enabled"])
        self.assertTrue(sandbox["failIfUnavailable"])
        self.assertTrue(sandbox["autoAllowBashIfSandboxed"])
        self.assertFalse(sandbox["allowUnsandboxedCommands"])
        self.assertEqual(sandbox["excludedCommands"], [])
        network = sandbox["network"]
        self.assertTrue(network["strictAllowlist"])
        self.assertNotIn("allow", network)
        # The documented key is allowedDomains with plain string entries.
        self.assertEqual(network["allowedDomains"], list(PACKAGE_REGISTRY_DOMAINS))
        self.assertEqual(len(PACKAGE_REGISTRY_DOMAINS), 7)
        for domain in ("registry.npmjs.org", "pypi.org", "files.pythonhosted.org", "static.crates.io"):
            self.assertIn(domain, PACKAGE_REGISTRY_DOMAINS)

    def test_execution_args_keep_permissions_and_user_configuration(self):
        ending = {"type": "object", "additionalProperties": False}
        args = execution_args(session_id="1b2f9c34-1111-4222-8333-444455556666", model="claude-opus-5",
                              effort="high", settings_path="/private/settings.json", read_only=False,
                              output_schema=ending)
        for flag, value in (("-p", None), ("--input-format", "stream-json"), ("--output-format", "stream-json"),
                            ("--verbose", None), ("--no-session-persistence", None),
                            ("--restricted", None), ("--permission-prompt-tool", "stdio"),
                            ("--settings", "/private/settings.json"),
                            ("--session-id", "1b2f9c34-1111-4222-8333-444455556666"),
                            ("--model", "claude-opus-5"), ("--effort", "high"),
                            ("--json-schema", canonical_json(ending))):
            if value is None:
                self.assertIn(flag, args)
            else:
                self.assertEqual(flag_value(args, flag), value, flag)
        for flag in ("--safe-mode", "--strict-mcp-config", "--mcp-config", "--setting-sources"):
            self.assertNotIn(flag, args)
        self.assertNotIn("--resume", args)
        self.assertEqual(flag_value(args, "--tools"), ",".join(WRITABLE_TOOLS))
        self.assertEqual(flag_value(args, "--permission-mode"), "acceptEdits")
        self.assertIsNone(flag_value(args, "--disallowedTools"))
        self.assertNotIn("bypassPermissions", args)
        self.assertIn("Bash", WRITABLE_TOOLS)
        self.assertIn("Write", WRITABLE_TOOLS)

    def test_default_effort_omits_the_effort_flag(self):
        args = execution_args(session_id="1b2f9c34-1111-4222-8333-444455556666", model="claude-haiku-4-5",
                              effort=DEFAULT_EFFORT, settings_path="/s.json", read_only=False,
                              output_schema={"type": "object"})
        self.assertNotIn("--effort", args)
        self.assertIsNone(flag_value(args, "--effort"))

    def test_read_only_execution_denies_writes_through_the_permission_layer(self):
        args = execution_args(session_id="1b2f9c34-1111-4222-8333-444455556666", model="claude-opus-5",
                              effort="high", settings_path="/s.json", read_only=True,
                              output_schema={"type": "object"})
        self.assertEqual(flag_value(args, "--tools"), ",".join(READONLY_TOOLS))
        self.assertEqual(set(READONLY_TOOLS) & {"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"}, set())
        self.assertEqual(flag_value(args, "--permission-mode"), "default")
        self.assertEqual(flag_value(args, "--disallowedTools").split(","),
                         ["Bash", "Edit", "MultiEdit", "NotebookEdit", "Write"])
        self.assertNotIn("bypassPermissions", args)
        self.assertNotIn("acceptEdits", args)

    def test_discovery_args_initialize_only(self):
        args = discovery_args()
        self.assertIn("-p", args)
        self.assertEqual(flag_value(args, "--input-format"), "stream-json")
        self.assertEqual(flag_value(args, "--output-format"), "stream-json")
        self.assertIn("--verbose", args)
        self.assertIn("--no-session-persistence", args)
        for flag in ("--safe-mode", "--strict-mcp-config", "--mcp-config", "--setting-sources"):
            self.assertNotIn(flag, args)
        for flag in ("--session-id", "--model", "--effort", "--resume", "--json-schema",
                     "--permission-prompt-tool", "--settings", "--tools"):
            self.assertNotIn(flag, args, flag)

    def test_native_environment_is_a_bounded_allowlist(self):
        env = {"PATH": "/bin", "HOME": "/users/fixture", "TMPDIR": "/tmp/fixture", "LANG": "C",
               "ANTHROPIC_API_KEY": "first-party-key", "CLAUDE_CONFIG_DIR": "/users/fixture/.claude",
               "SSL_CERT_FILE": "/certs/root.pem", "SSL_CERT_DIR": "/certs/dirs",
               "REQUESTS_CA_BUNDLE": "/certs/bundle.pem", "CURL_CA_BUNDLE": "/certs/curl.pem",
               "NODE_EXTRA_CA_CERTS": "/certs/node.pem",
               "CLAUDECODE": "1", "CLAUDE_CODE_EFFORT_LEVEL": "max", "ANTHROPIC_MODEL": "claude-other",
               "ANTHROPIC_AUTH_TOKEN": "bearer-secret", "ANTHROPIC_BASE_URL": "https://gateway.example.invalid",
               "BUDDY_AGENT_CREDENTIAL": "worker-secret", "BUDDY_AGENT_CREDENTIAL_FILE": "/private/cred.json",
               "BUDDY_STATE_DIR": "/private/state", "SOME_OTHER_HARNESS_TOKEN": "peer-secret",
               "BUDDY_CLAUDE_FIXTURE_CASE": "ok"}
        with mock.patch('hey_my_buddy.buddy.harnesses.claude.config.cli_command', return_value=[]):
            result = native_environment(env)
        self.assertEqual(result["PATH"], "/bin")
        self.assertEqual(result["HOME"], "/users/fixture")
        self.assertEqual(result["TMPDIR"], "/tmp/fixture")
        # Certificate paths name trust roots, not credentials, and survive.
        self.assertEqual(result["SSL_CERT_FILE"], "/certs/root.pem")
        self.assertEqual(result["SSL_CERT_DIR"], "/certs/dirs")
        self.assertEqual(result["REQUESTS_CA_BUNDLE"], "/certs/bundle.pem")
        self.assertEqual(result["CURL_CA_BUNDLE"], "/certs/curl.pem")
        self.assertEqual(result["NODE_EXTRA_CA_CERTS"], "/certs/node.pem")
        self.assertNotIn('ANTHROPIC_API_KEY', result)
        self.assertEqual(result["CLAUDE_CONFIG_DIR"], "/users/fixture/.claude")
        self.assertEqual(result["BUDDY_CLAUDE_FIXTURE_CASE"], "ok")
        # No host, worker, agent-credential or other-harness value may leak.
        for banned in ("CLAUDECODE", "CLAUDE_CODE_EFFORT_LEVEL", "ANTHROPIC_MODEL", "ANTHROPIC_AUTH_TOKEN",
                       "ANTHROPIC_BASE_URL", "BUDDY_AGENT_CREDENTIAL", "BUDDY_AGENT_CREDENTIAL_FILE",
                       "BUDDY_STATE_DIR", "SOME_OTHER_HARNESS_TOKEN"):
            self.assertNotIn(banned, result)

    def test_subagent_tool_list_names_the_current_agent_tool(self):
        self.assertIn("Agent", WRITABLE_TOOLS)
        self.assertIn("Task", WRITABLE_TOOLS)
        self.assertIn("Bash", WRITABLE_TOOLS)


if __name__ == "__main__":
    unittest.main()
