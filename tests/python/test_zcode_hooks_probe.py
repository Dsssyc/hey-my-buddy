"""Focused tests for the P3 ZCode hooks probe (tests/probes/zcode_hooks.py).

These tests are free: they never start a native model prompt and never write
outside per-test temporary directories. They verify the probe's isolation
machinery itself — credential scrubbing, snapshot/diff, hook self-test, private
environment construction — and, when the installed bundle is present, a free
``--version`` run plus the full unpaid probe pipeline.
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PROBE_PATH = Path(__file__).resolve().parents[1] / "probes" / "zcode_hooks.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("zcode_hooks_probe", _PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


class RedactionTests(unittest.TestCase):
    def test_redact_text_hides_credential_shapes(self):
        text = 'cfg {"apiKey": "zai-abcdefghijklmnop1234"} bearer Bearer tok12345678 sk-live-abcdef0123456789'
        redacted = probe.redact_text(text)
        self.assertNotIn("zai-abcdefghijklmnop1234", redacted)
        self.assertNotIn("tok12345678", redacted)
        self.assertNotIn("abcdef0123456789", redacted)
        self.assertIn("<redacted", redacted)

    def test_redact_text_keeps_plain_content(self):
        text = "PROBE_NONCE=P3HOOK-0123456789abcdef exit_code=0"
        self.assertEqual(text, probe.redact_text(text))


class EnvironmentIsolationTests(unittest.TestCase):
    def test_scrub_environment_clears_buddy_credentials(self):
        fake = {"PATH": "/usr/bin:/bin", "VIRTUAL_ENV": "/some/venv"}
        for var in probe.CREDENTIAL_ENV_VARS:
            fake[var] = f"leak-{var}"
        scrubbed = probe.scrub_environment(fake)
        for var in probe.CREDENTIAL_ENV_VARS:
            self.assertNotIn(var, scrubbed)
        self.assertEqual(scrubbed["PATH"], "/usr/bin:/bin")

    def test_private_environment_uses_private_paths_and_never_inherits_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            fake = {
                "HOME": tmp,
                "PATH": "/usr/bin:/bin",
                "BUDDY_AGENT_CREDENTIAL": "leak-credential",
                "BUDDY_WORKER_ID": "leak-worker",
                "UV_PROJECT_ENVIRONMENT": "/leak/venv",
                "HTTP_PROXY": "http://127.0.0.1:9",
            }
            with mock.patch.dict(os.environ, fake, clear=True):
                env = probe.build_private_environment(work, require_provider=False)
            self.assertEqual(env["ZCODE_STORAGE_DIR"], str(work / "storage"))
            self.assertEqual(env["ZCODE_LOG_DIR"], str(work / "log"))
            self.assertEqual(env["ZCODE_SESSION_DB_PATH"], str(work / "sessions.sqlite"))
            for var in probe.CREDENTIAL_ENV_VARS:
                self.assertNotIn(var, env)
            self.assertEqual(env["HTTP_PROXY"], "http://127.0.0.1:9")

    def test_paid_environment_requires_provider_vars(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                probe.build_private_environment(Path(tmp), require_provider=True)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_and_diff_detect_every_change_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("one", encoding="utf-8")
            (root / "b.txt").write_text("two", encoding="utf-8")
            before = probe.snapshot_tree(str(root))
            (root / "a.txt").write_text("one-changed", encoding="utf-8")
            (root / "c.txt").write_text("three", encoding="utf-8")
            (root / "b.txt").unlink()
            diff = probe.diff_snapshots(before, probe.snapshot_tree(str(root)))
            self.assertEqual(diff["changed"], ["a.txt"])
            self.assertEqual(diff["added"], ["c.txt"])
            self.assertEqual(diff["removed"], ["b.txt"])

    def test_snapshot_of_missing_root_is_empty(self):
        self.assertEqual(probe.snapshot_tree("/nonexistent/probe-root-xyz"), {})


class HookAssemblyTests(unittest.TestCase):
    def test_config_document_shape(self):
        config = probe.config_document("/tmp/hook.py", "/tmp/log.jsonl", "NONCE", "PostToolUse", "run-token")
        self.assertTrue(config["hooks"]["enabled"])
        entry = config["hooks"]["events"]["PostToolUse"][0]
        self.assertEqual(entry["matcher"], "Bash")
        hook = entry["hooks"][0]
        self.assertEqual(hook["type"], "process")
        self.assertEqual(hook["args"][2], "NONCE")
        self.assertEqual(hook["args"][3], "PostToolUse")
        self.assertEqual(hook["args"][4], "run-token")

    def test_write_config_embeds_nonce_and_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            parts = probe.assemble_workspace(work, "config")
            nonce = probe.NONCE_PREFIX + "0011223344556677"
            config_path = probe.write_config(work, parts, nonce)
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            hook = loaded["hooks"]["events"]["PostToolUse"][0]["hooks"][0]
            self.assertEqual(hook["args"][2], nonce)
            self.assertEqual(hook["args"][3], "PostToolUse")
            self.assertEqual(hook["args"][4], parts["run_token"])

    def test_plugin_mode_assembles_inline_plugin_and_workspace_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            parts = probe.assemble_workspace(work, "plugin")
            nonce = probe.NONCE_PREFIX + "9988776655443322"
            hooks_json = probe.write_plugin(parts, nonce)
            self.assertTrue(parts["plugin_root"].exists())
            manifest = json.loads(
                (parts["plugin_root"] / ".zcode-plugin" / "plugin.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["name"], probe.PLUGIN_NAME)
            self.assertEqual(manifest["hooks"], "hooks/hooks.json")
            hooks_doc = json.loads(hooks_json.read_text(encoding="utf-8"))
            hook = hooks_doc["hooks"]["PostToolUse"][0]["hooks"][0]
            self.assertEqual(hook["args"][2], nonce)
            self.assertEqual(hook["args"][3], "PostToolUse")
            workspace = json.loads(parts["workspace_config"].read_text(encoding="utf-8"))
            self.assertEqual(workspace["plugins"]["dirs"], [str(parts["plugin_root"])])
            # Plugin mode must not write a hooks block into the storage config.
            self.assertIsNone(parts["config"])

    def test_hook_script_selftest_records_and_injects_nonce(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            parts = probe.assemble_workspace(work, "config")
            nonce = probe.NONCE_PREFIX + "aabbccdd11223344"
            result = probe.selftest_hook(parts, nonce)
            self.assertEqual(result["exit_code"], 0, result["stderr_head"])
            self.assertTrue(result["stdout_is_valid_context"])
            self.assertEqual(len(result["log_records"]), 1)
            record = result["log_records"][0]
            self.assertEqual(record["nonce"], nonce)
            self.assertEqual(record["run_token"], parts["run_token"])
            self.assertEqual(record["hook_event_name"], "PostToolUse")
            self.assertEqual(record["tool_name"], "Bash")
            self.assertIsNotNone(record["tool_input_sha256"])
            # The redaction-by-construction rule: raw tool input never reaches the log.
            self.assertNotIn("echo hi", json.dumps(record))


class FreeProbePipelineTests(unittest.TestCase):
    BUNDLE = probe.DEFAULT_BUNDLE

    def setUp(self):
        if not Path(self.BUNDLE).exists() or not probe.resolve_node(None):
            self.skipTest("native bundle or node runtime not present")

    def test_free_probe_pipeline_is_isolated_and_selftested(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "probe-work"
            global_root = Path(tmp) / "fake-global-root"
            global_root.mkdir()
            (global_root / "keep.txt").write_text("stable", encoding="utf-8")
            summary = probe.run_probe(
                bundle=self.BUNDLE,
                node=probe.resolve_node(None),
                work_dir=str(work),
                paid=False,
                timeout=60,
                global_root=str(global_root),
                hook_via="plugin",
            )
            self.assertTrue(summary["verdict"]["hook_selftest_ok"])
            self.assertTrue(summary["verdict"]["global_root_unchanged"])
            self.assertTrue(summary["verdict"]["plugin_listed_free"])
            self.assertEqual(summary["global_root_diff"], {"added": [], "removed": [], "changed": []})
            self.assertEqual(summary["version_run"]["exit_code"], 0)
            self.assertEqual(summary["plugins_list_run"]["exit_code"], 0)
            self.assertIn("cli", summary["versions"])
            self.assertTrue((work / "summary.json").exists())
            joined_files = " ".join(summary["private_work_files"])
            self.assertIn("plugin/hooks/hooks.json", joined_files)
            self.assertIn("cwd/.zcode/config.json", joined_files)

    def test_gather_versions_reports_native_identities(self):
        versions = probe.gather_versions(probe.resolve_node(None), self.BUNDLE)
        self.assertTrue(versions["node"].startswith("v"))
        self.assertEqual(len(versions["bundle_sha256"]), 64)
        self.assertRegex(versions["cli"], r"\d+\.\d+\.\d+")


if __name__ == "__main__":
    unittest.main()
