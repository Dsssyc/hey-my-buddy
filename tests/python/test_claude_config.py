"""Claude configuration policy unit tests; no real Claude Code CLI process is started."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from buddy.adapters import claude_config
from buddy.adapters.claude_config import (AUTH_STATUS_ARGUMENTS, AUTH_STATUS_MAX_BYTES,
                                          AUTH_STATUS_TIMEOUT_SECONDS, ClaudeUnavailable, DEFAULT_EFFORT,
                                          EMPTY_MCP_CONFIG, MODEL_FALLBACK_VARIABLES, NATIVE_ENVIRONMENT_ALLOWLIST,
                                          PACKAGE_REGISTRY_DOMAINS, READONLY_TOOLS, THIRD_PARTY_OVERRIDE_VARIABLES,
                                          WRITABLE_TOOLS, account_problem, auth_status_problem, cli_command,
                                          discovery_args, execution_args, native_environment, read_auth_status,
                                          sandbox_settings, settings_policy, third_party_overrides,
                                          token_source_missing)
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

    def test_missing_or_null_token_source_is_the_only_fallback_shape(self):
        self.assertTrue(token_source_missing({"apiProvider": "firstParty", "tokenSource": None}))
        self.assertTrue(token_source_missing({"apiProvider": "firstParty"}))
        self.assertTrue(token_source_missing({"apiProvider": "anthropic"}))
        for account in ({"apiProvider": "firstParty", "tokenSource": "subscription"},
                        {"apiProvider": "firstParty", "tokenSource": "none"},
                        {"apiProvider": "firstParty", "tokenSource": ""},
                        {"apiProvider": "firstParty", "tokenSource": 7},
                        {"apiProvider": "bedrock", "tokenSource": None},
                        {}, None):
            self.assertFalse(token_source_missing(account))
        # The gate itself keeps refusing every non-login shape; only the runner
        # re-checks the eligible shape through the bounded readback.
        self.assertIn("tokenSource none", account_problem({"apiProvider": "firstParty", "tokenSource": None}))

    def test_auth_status_problem_requires_exactly_logged_in_first_party(self):
        self.assertIsNone(auth_status_problem({"loggedIn": True, "authMethod": "claude.ai",
                                               "apiProvider": "firstParty", "subscriptionType": "pro"}))
        self.assertIsNone(auth_status_problem({"loggedIn": True, "apiProvider": "anthropic"}))
        for payload in (None, [], "logged in", 3, {}, {"loggedIn": False, "apiProvider": "firstParty"},
                        {"loggedIn": "true", "apiProvider": "firstParty"},
                        {"loggedIn": 1, "apiProvider": "firstParty"},
                        {"loggedIn": True, "apiProvider": "bedrock"},
                        {"loggedIn": True, "apiProvider": None}, {"loggedIn": True}):
            reason = auth_status_problem(payload)
            self.assertTrue(reason)
            self.assertLessEqual(len(reason), 200)

    def test_read_auth_status_fails_closed_with_bounded_reasons(self):
        with tempfile.TemporaryDirectory(prefix="buddy-claude-auth-") as temp:
            made = [0]

            def fake_cli(body: str) -> list[str]:
                made[0] += 1
                path = Path(temp) / f"cli-{made[0]}"
                path.write_text("#!/bin/sh\n" + body + "\n")
                path.chmod(0o755)
                return [str(path)]

            environment = {"PATH": "/usr/bin:/bin", "HOME": temp}
            self.assertIsNone(read_auth_status(
                fake_cli('printf \'%s\' \'{"loggedIn":true,"authMethod":"claude.ai",'
                         '"apiProvider":"firstParty","subscriptionType":"pro"}\''),
                cwd=temp, environment=environment))
            for body, expected in (("exit 3", "exited nonzero"),
                                   ("printf '%s' '{not json'", "not valid JSON"),
                                   ("printf '%s' '{\"loggedIn\":false,\"apiProvider\":\"firstParty\"}'",
                                    "active first-party login"),
                                   ("printf '%s' '{\"loggedIn\":true,\"apiProvider\":\"bedrock\"}'",
                                    "not first-party"),
                                   ("printf '%s' '{}'", "active first-party login")):
                reason = read_auth_status(fake_cli(body), cwd=temp, environment=environment)
                self.assertIn(expected, reason)
                self.assertLessEqual(len(reason), 200)
            self.assertIn("output bound", read_auth_status(fake_cli("yes x | head -c 1048577"),
                                                           cwd=temp, environment=environment))
            self.assertIn("time bound", read_auth_status(fake_cli("sleep 5"), cwd=temp,
                                                         environment=environment, timeout=0.3))
            secret = read_auth_status(fake_cli("printf '%s' '{}'"), cwd=temp,
                                      environment={"PATH": "/usr/bin:/bin",
                                                   "ANTHROPIC_BASE_URL": "https://secret-gateway.example.invalid"})
            self.assertIn("third-party provider overrides", secret)
            self.assertNotIn("secret-gateway", secret)

    def test_auth_status_probe_arguments_and_bounds_are_fixed(self):
        self.assertEqual(AUTH_STATUS_ARGUMENTS, ("auth", "status", "--json"))
        self.assertGreaterEqual(AUTH_STATUS_TIMEOUT_SECONDS, 5)
        self.assertLessEqual(AUTH_STATUS_TIMEOUT_SECONDS, 30)
        self.assertGreaterEqual(AUTH_STATUS_MAX_BYTES, 1 << 16)

    def test_ambiguous_or_nonfinite_auth_status_cannot_prove_login(self):
        malformed = [
            b'{"loggedIn":false,"loggedIn":true,"apiProvider":"firstParty"}',
            b'{"loggedIn":true,"apiProvider":"firstParty","unused":NaN}',
        ]
        for payload in malformed:
            with self.subTest(size=len(payload)), mock.patch.object(
                    claude_config.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=payload)):
                self.assertEqual(read_auth_status(["/fixture/claude"], cwd="/", environment={}),
                                 "the Claude auth status readback was not valid JSON")
        with mock.patch.object(claude_config.subprocess, "run", return_value=mock.Mock(
                returncode=0, stdout=b'[' * 30000 + b']' * 30000)):
            self.assertIsNotNone(read_auth_status(["/fixture/claude"], cwd="/", environment={}))

    def test_settings_policy_defaults_to_isolated_and_rejects_other_values(self):
        self.assertEqual(settings_policy({"BUDDY_CLAUDE_SETTINGS_POLICY": "isolated"}), "isolated")
        self.assertEqual(settings_policy({}), "isolated")
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
