#!/usr/bin/env python3
"""Explicit unpaid installed-ZCode version and inline-plugin checks.

Run from the checkout root:
    PYTHONPATH=src:tests/python python tests/probes/zcode_hooks_native.py -v
Select one check with FreeProbePipelineTests.<test_name>.

This runs Node --version, the installed ZCode bundle with --version and
plugins list, and our local Python hook self-test. It uses a private HOME,
provider configuration and storage, and hashes only a temporary global-root
fixture. It never prompts a model, calls a provider or reads user credentials.
The real bundle identity and native plugin-loading assertion require an
installed ZCode and Node; missing prerequisites fail rather than skip.
"""
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PROBE_PATH = Path(__file__).with_name("zcode_hooks.py")
_spec = importlib.util.spec_from_file_location("zcode_hooks_probe", _PROBE_PATH)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


class FreeProbePipelineTests(unittest.TestCase):
    BUNDLE = probe.DEFAULT_BUNDLE

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="zcode-hooks-native-test-")
        self.addCleanup(temporary.cleanup)
        self.private_root = Path(temporary.name)
        home = self.private_root / "home"
        home.mkdir(mode=0o700)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("HMB_", "ZCODE_", "ZAI_", "ZHIPU_", "GLM_"))
                       and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        environment.update(HOME=str(home), USERPROFILE=str(home), ZCODE_DATA_BASE_DIR=str(home))
        self.enterContext(mock.patch.dict(os.environ, environment, clear=True))

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
