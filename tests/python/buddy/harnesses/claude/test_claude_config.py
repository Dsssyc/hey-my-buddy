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
    worker_sandbox_settings,
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
    "HMB_STATE_DIR", "HMB_RUNTIME_ROOT", "HMB_RUNTIME", "HMB_RUNTIME_IDENTITY",
    "HMB_WORKER_STATE", "HMB_WORKER_ID",
    "HMB_AGENT_CREDENTIAL_FILE", "HMB_HARNESS_RECORD_FILE",
    "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT",
)


class ClaudeConfigTests(unittest.TestCase):
    def test_session_mcp_uses_native_mount_and_tool_permission(self):
        tools = ("mcp__attempt__hey_my_buddy_checkpoint", "mcp__attempt__hey_my_buddy_answer_inquiry")
        args = execution_args(session_id="session", model="model", effort="default",
                              settings_path="settings.json", read_only=True, output_schema={},
                              mcp_config_path="inquiry-mcp.json", inquiry_tools=tools)
        self.assertEqual(flag_value(args, "--mcp-config"), "inquiry-mcp.json")
        self.assertEqual(flag_value(args, "--allowedTools"), ",".join(tools))
        self.assertIn("ToolSearch", flag_value(args, "--tools").split(","))
        self.assertNotIn(tools[0], flag_value(args, "--tools").split(","))
        self.assertNotIn("--strict-mcp-config", args)
        self.assertNotIn("--setting-sources", args)

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
        environment["HMB_DEV_SOURCE"] = "1"
        patcher = mock.patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cli_override_and_missing_cli(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-claude-config-") as temp:
            cli = Path(temp) / "claude"
            cli.write_text("#!/bin/sh\n")
            cli.chmod(0o755)
            self.assertEqual(cli_command({"HMB_CLAUDE_CLI": str(cli), "PATH": tempfile.mkdtemp()}),
                             [str(cli.resolve())])
            with tempfile.TemporaryDirectory(prefix="hey-my-buddy-claude-empty-") as empty:
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

    def test_worker_settings_allow_bash_only_for_checkout_write(self):
        self.assertEqual(worker_sandbox_settings("write"), {
            "permissions": {"allow": ["Bash"]},
            "sandbox": {
                "enabled": True, "failIfUnavailable": True, "autoAllowBashIfSandboxed": True,
                "allowUnsandboxedCommands": False, "excludedCommands": [],
                "network": {"strictAllowlist": False, "allowedDomains": ["*"],
                            "allowLocalBinding": True},
            },
        })
        self.assertEqual(worker_sandbox_settings("read"), sandbox_settings())
        self.assertNotIn("permissions", worker_sandbox_settings("read"))
        self.assertEqual(worker_sandbox_settings("full"), {"sandbox": {"enabled": False}})

    def test_router_settings_preserve_each_native_setting(self):
        for domains in (None, ()):
            with self.subTest(domains=domains):
                self.assertEqual(sandbox_settings(domains), {"sandbox": {
                    "enabled": True, "failIfUnavailable": True, "autoAllowBashIfSandboxed": True,
                    "allowUnsandboxedCommands": False, "excludedCommands": [],
                    "network": {"strictAllowlist": True,
                                "allowedDomains": list(PACKAGE_REGISTRY_DOMAINS) if domains is None else []},
                }})

    def test_execution_args_keep_permissions_and_user_configuration(self):
        ending = {"type": "object", "additionalProperties": False}
        args = execution_args(session_id="1b2f9c34-1111-4222-8333-444455556666", model="claude-opus-5",
                              effort="high", settings_path="/private/settings.json", read_only=False,
                              output_schema=ending)
        for flag, value in (("-p", None), ("--input-format", "stream-json"), ("--output-format", "stream-json"),
                            ("--verbose", None), ("--no-session-persistence", None),
                            ("--permission-prompt-tool", "stdio"),
                            ("--settings", "/private/settings.json"),
                            ("--session-id", "1b2f9c34-1111-4222-8333-444455556666"),
                            ("--model", "claude-opus-5"), ("--effort", "high"),
                            ("--json-schema", canonical_json(ending))):
            if value is None:
                self.assertIn(flag, args)
            else:
                self.assertEqual(flag_value(args, flag), value, flag)
        for flag in ("--restricted", "--safe-mode", "--strict-mcp-config", "--mcp-config", "--setting-sources"):
            self.assertNotIn(flag, args)
        self.assertNotIn("--resume", args)
        self.assertEqual(flag_value(args, "--tools"), ",".join(WRITABLE_TOOLS))
        self.assertEqual(flag_value(args, "--permission-mode"), "acceptEdits")
        self.assertIsNone(flag_value(args, "--disallowedTools"))
        self.assertNotIn("bypassPermissions", args)
        self.assertIn("Bash", WRITABLE_TOOLS)
        self.assertIn("Write", WRITABLE_TOOLS)
        self.assertIn("Skill", flag_value(args, "--tools").split(","))

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
        self.assertIn("Skill", flag_value(args, "--tools").split(","))
        self.assertNotIn("--restricted", args)
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
               "HMB_AGENT_CREDENTIAL_FILE": "/private/cred.json",
               "HMB_STATE_DIR": "/private/state", "SOME_OTHER_HARNESS_TOKEN": "peer-secret",
               "HMB_CLAUDE_FIXTURE_CASE": "ok"}
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
        self.assertEqual(result["HMB_CLAUDE_FIXTURE_CASE"], "ok")
        # No host, worker, agent-credential or other-harness value may leak.
        for banned in ("CLAUDECODE", "CLAUDE_CODE_EFFORT_LEVEL", "ANTHROPIC_MODEL", "ANTHROPIC_AUTH_TOKEN",
                       "ANTHROPIC_BASE_URL", "HMB_AGENT_CREDENTIAL_FILE",
                       "HMB_STATE_DIR", "SOME_OTHER_HARNESS_TOKEN"):
            self.assertNotIn(banned, result)

    def test_subagent_tool_list_names_the_current_agent_tool(self):
        self.assertIn("Agent", WRITABLE_TOOLS)
        self.assertIn("Task", WRITABLE_TOOLS)
        self.assertIn("Bash", WRITABLE_TOOLS)


if __name__ == "__main__":
    unittest.main()
