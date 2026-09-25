"""Claude configuration policy unit tests; no native process is started."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from buddy.adapters import claude_config
from buddy.adapters.claude_config import (ClaudeUnavailable, DEFAULT_EFFORT, EMPTY_MCP_CONFIG,
                                          MODEL_FALLBACK_VARIABLES, NATIVE_ENVIRONMENT_ALLOWLIST,
                                          PACKAGE_REGISTRY_DOMAINS, READONLY_TOOLS, THIRD_PARTY_OVERRIDE_VARIABLES,
                                          WRITABLE_TOOLS, account_problem, cli_command, discovery_args, execution_args,
                                          native_environment, sandbox_settings, settings_policy,
                                          third_party_overrides)
from buddy.adapters.claude_protocol import OUTCOME_SCHEMA
from buddy.adapters.turn_io import canonical_json


def flag_value(args: list[str], flag: str) -> str | None:
    for index, item in enumerate(args):
        if item == flag and index + 1 < len(args):
            return args[index + 1]
        if item.startswith(flag + "="):
            return item[len(flag) + 1:]
    return None


class ClaudeConfigTests(unittest.TestCase):
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

    def test_third_party_overrides_report_names_only(self):
        env = {"ANTHROPIC_BASE_URL": "https://secret-gateway.example.invalid", "CLAUDE_CODE_USE_BEDROCK": "1",
               "ANTHROPIC_AWS_BASE_URL": "https://aws.example.invalid",
               "ANTHROPIC_CUSTOM_HEADERS": "X-Secret: 1",
               "ANTHROPIC_BEDROCK_MANTLE_BASE_URL": "https://mantle.example.invalid", "PATH": "/bin"}
        names = third_party_overrides(env)
        self.assertEqual(names, ["ANTHROPIC_BASE_URL", "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
                                 "ANTHROPIC_AWS_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS", "CLAUDE_CODE_USE_BEDROCK"])
        # The refusal reason names the variables but never the credential or URL values.
        reason = "Claude refuses third-party provider overrides: " + ", ".join(names)
        self.assertNotIn("secret-gateway", reason)
        for documented in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AWS_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
                           "ANTHROPIC_BEDROCK_MANTLE_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL",
                           "ANTHROPIC_FOUNDRY_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                           "CLAUDE_CODE_USE_FOUNDRY"):
            self.assertIn(documented, THIRD_PARTY_OVERRIDE_VARIABLES)

    def test_account_problem_requires_first_party_login(self):
        self.assertIsNone(account_problem({"apiProvider": "anthropic", "tokenSource": "subscription"}))
        self.assertIsNone(account_problem({"apiProvider": "firstParty", "tokenSource": "subscription"}))
        self.assertIsNone(account_problem({"apiProvider": "firstParty", "tokenSource": "api-key"}))
        self.assertIn("first-party", account_problem({"apiProvider": "bedrock", "tokenSource": "api-key"}))
        self.assertIn("tokenSource none", account_problem({"apiProvider": "firstParty", "tokenSource": "none"}))
        self.assertIsNotNone(account_problem(None))

    def test_settings_policy_requires_exact_isolated_value(self):
        self.assertEqual(settings_policy({"BUDDY_CLAUDE_SETTINGS_POLICY": "isolated"}), "isolated")
        self.assertIsNone(settings_policy({}))
        self.assertIsNone(settings_policy({"BUDDY_CLAUDE_SETTINGS_POLICY": "global"}))
        self.assertIsNone(settings_policy({"BUDDY_CLAUDE_SETTINGS_POLICY": ""}))

    def test_sandbox_settings_use_the_documented_network_keys(self):
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

    def test_execution_args_compose_the_strict_isolated_invocation(self):
        args = execution_args(session_id="1b2f9c34-1111-4222-8333-444455556666", model="claude-opus-5",
                              effort="high", settings_path="/private/settings.json", read_only=False)
        for flag, value in (("-p", None), ("--input-format", "stream-json"), ("--output-format", "stream-json"),
                            ("--verbose", None), ("--safe-mode", None), ("--strict-mcp-config", None),
                            ("--mcp-config", EMPTY_MCP_CONFIG), ("--setting-sources", ""),
                            ("--restricted", None), ("--permission-prompt-tool", "stdio"),
                            ("--settings", "/private/settings.json"),
                            ("--session-id", "1b2f9c34-1111-4222-8333-444455556666"),
                            ("--model", "claude-opus-5"), ("--effort", "high"),
                            ("--json-schema", canonical_json(OUTCOME_SCHEMA))):
            if value is None:
                self.assertIn(flag, args)
            else:
                self.assertEqual(flag_value(args, flag), value, flag)
        self.assertNotIn("--resume", args)
        self.assertEqual(flag_value(args, "--tools"), ",".join(WRITABLE_TOOLS))
        self.assertEqual(flag_value(args, "--permission-mode"), "acceptEdits")
        self.assertIsNone(flag_value(args, "--disallowedTools"))
        self.assertNotIn("bypassPermissions", args)
        self.assertIn("Bash", WRITABLE_TOOLS)
        self.assertIn("Write", WRITABLE_TOOLS)

    def test_default_effort_omits_the_effort_flag(self):
        args = execution_args(session_id="1b2f9c34-1111-4222-8333-444455556666", model="claude-haiku-4-5",
                              effort=DEFAULT_EFFORT, settings_path="/s.json", read_only=False)
        self.assertNotIn("--effort", args)
        self.assertIsNone(flag_value(args, "--effort"))

    def test_read_only_execution_denies_writes_through_the_permission_layer(self):
        args = execution_args(session_id="1b2f9c34-1111-4222-8333-444455556666", model="claude-opus-5",
                              effort="high", settings_path="/s.json", read_only=True)
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
        self.assertIn("--safe-mode", args)
        self.assertIn("--no-session-persistence", args)
        self.assertIn("--strict-mcp-config", args)
        self.assertEqual(flag_value(args, "--mcp-config"), EMPTY_MCP_CONFIG)
        self.assertEqual(flag_value(args, "--setting-sources"), "")
        for flag in ("--session-id", "--model", "--effort", "--resume", "--json-schema",
                     "--permission-prompt-tool", "--settings", "--tools"):
            self.assertNotIn(flag, args, flag)

    def test_native_environment_is_a_bounded_allowlist(self):
        env = {"PATH": "/bin", "HOME": "/users/fixture", "TMPDIR": "/tmp/fixture", "LANG": "C",
               "ANTHROPIC_API_KEY": "first-party-key", "CLAUDE_CONFIG_DIR": "/users/fixture/.claude",
               "CLAUDECODE": "1", "CLAUDE_CODE_EFFORT_LEVEL": "max", "ANTHROPIC_MODEL": "claude-other",
               "ANTHROPIC_AUTH_TOKEN": "bearer-secret", "ANTHROPIC_BASE_URL": "https://gateway.example.invalid",
               "BUDDY_AGENT_CREDENTIAL": "worker-secret", "BUDDY_AGENT_CREDENTIAL_FILE": "/private/cred.json",
               "BUDDY_STATE_DIR": "/private/state", "SOME_OTHER_HARNESS_TOKEN": "peer-secret",
               "BUDDY_CLAUDE_FIXTURE_CASE": "ok"}
        result = native_environment(env)
        self.assertEqual(result["PATH"], "/bin")
        self.assertEqual(result["HOME"], "/users/fixture")
        self.assertEqual(result["TMPDIR"], "/tmp/fixture")
        self.assertEqual(result["ANTHROPIC_API_KEY"], "first-party-key")
        self.assertEqual(result["CLAUDE_CONFIG_DIR"], "/users/fixture/.claude")
        self.assertEqual(result["BUDDY_CLAUDE_FIXTURE_CASE"], "ok")
        # No host, worker, agent-credential or other-harness value may leak.
        for banned in ("CLAUDECODE", "CLAUDE_CODE_EFFORT_LEVEL", "ANTHROPIC_MODEL", "ANTHROPIC_AUTH_TOKEN",
                       "ANTHROPIC_BASE_URL", "BUDDY_AGENT_CREDENTIAL", "BUDDY_AGENT_CREDENTIAL_FILE",
                       "BUDDY_STATE_DIR", "SOME_OTHER_HARNESS_TOKEN"):
            self.assertNotIn(banned, result)
        self.assertEqual(set(result) - set(NATIVE_ENVIRONMENT_ALLOWLIST), {"BUDDY_CLAUDE_FIXTURE_CASE"})
        # The fallback set is documentation for what the allowlist must exclude.
        self.assertEqual(set(MODEL_FALLBACK_VARIABLES) & set(NATIVE_ENVIRONMENT_ALLOWLIST), set())
        self.assertIn("CLAUDE_CODE_EFFORT_LEVEL", MODEL_FALLBACK_VARIABLES)
        self.assertIn("ANTHROPIC_MODEL", MODEL_FALLBACK_VARIABLES)

    def test_subagent_tool_list_names_the_current_agent_tool(self):
        self.assertIn("Agent", WRITABLE_TOOLS)
        self.assertIn("Task", WRITABLE_TOOLS)
        self.assertIn("Bash", WRITABLE_TOOLS)


if __name__ == "__main__":
    unittest.main()
