"""First-install distribution: repository catalog, staged marketplace and docs.

The catalog schema, the CLI surface and the ``CODEX_HOME`` isolation asserted here
were verified against the installed Codex CLI (``codex plugin --help``,
``codex plugin marketplace add/list``) and the official plugin documentation. The
real-CLI test runs with a private ``CODEX_HOME`` and asserts that the operator's
``~/.codex`` configuration is byte-identical afterwards; it skips when the Codex CLI
is not installed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_packaging import ROOT, load_stage_plugin

CATALOG = ROOT / ".agents" / "plugins" / "marketplace.json"
PLUGIN_NAME = "hey-my-buddy"
INSTALLATION_POLICIES = {"NOT_AVAILABLE", "AVAILABLE", "INSTALLED_BY_DEFAULT"}
AUTHENTICATION_POLICIES = {"ON_INSTALL", "ON_USE"}
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$")
OPERATIONS = ROOT / "docs" / "reference" / "operations.md"


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    assert isinstance(value, dict), f"{path} must contain one JSON object"
    return value


def resolved_entry(marketplace_root: Path, catalog: dict) -> Path:
    """The plugin directory a catalog entry points at, using the verified rules."""
    entry = catalog["plugins"][0]
    source = entry["source"]
    assert source["source"] == "local", source
    relative = source["path"]
    assert relative.startswith("./"), relative
    resolved = (marketplace_root / relative).resolve()
    assert resolved == marketplace_root.resolve() or marketplace_root.resolve() in resolved.parents, resolved
    return resolved


class RepositoryCatalogTests(unittest.TestCase):
    """The committed catalog is a complete, self-contained local/Git marketplace."""

    def test_catalog_is_present_and_shaped_like_the_verified_schema(self):
        self.assertTrue(CATALOG.is_file(), "the repository must ship its marketplace catalog")
        catalog = load_json(CATALOG)
        self.assertTrue(NAME_PATTERN.match(catalog["name"]), catalog["name"])
        self.assertEqual(catalog["name"], PLUGIN_NAME)
        self.assertTrue(catalog["interface"]["displayName"].strip())
        self.assertEqual(len(catalog["plugins"]), 1)
        entry = catalog["plugins"][0]
        self.assertEqual(entry["name"], PLUGIN_NAME)
        self.assertIn(entry["policy"]["installation"], INSTALLATION_POLICIES)
        self.assertIn(entry["policy"]["authentication"], AUTHENTICATION_POLICIES)
        self.assertTrue(entry["category"].strip())
        self.assertEqual(set(entry["source"]), {"source", "path"})

    def test_catalog_entry_resolves_inside_the_marketplace_root(self):
        catalog = load_json(CATALOG)
        resolved = resolved_entry(ROOT, catalog)
        self.assertEqual(resolved, ROOT.resolve())
        self.assertTrue((resolved / ".codex-plugin" / "plugin.json").is_file())
        self.assertTrue((resolved / "plugin.json").is_file())
        self.assertTrue((resolved / "skills" / "buddy" / "SKILL.md").is_file())

    def test_catalog_carries_no_personal_or_absolute_dependency(self):
        raw = CATALOG.read_text()
        self.assertNotIn(str(Path.home()), raw)
        self.assertNotIn("http://", raw)
        self.assertNotIn("https://", raw, "a local entry must not depend on a remote registry")
        self.assertNotIn("~", raw)
        # The same catalog works for a Git clone and for a local checkout path; no
        # entry may name a developer's personal marketplace directory.
        self.assertNotIn("personal", raw)

    def test_portable_and_codex_manifests_agree(self):
        codex = load_json(ROOT / ".codex-plugin" / "plugin.json")
        portable = load_json(ROOT / "plugin.json")
        self.assertEqual(codex["name"], portable["name"])
        self.assertEqual(codex["version"], portable["version"])
        self.assertEqual(codex["name"], PLUGIN_NAME)
        self.assertRegex(codex["version"], r"^\d+\.\d+\.\d+")
        for field in ("displayName", "shortDescription", "longDescription", "developerName", "category"):
            self.assertTrue(codex["interface"][field].strip(), field)
        self.assertTrue(codex["interface"].get("defaultPrompt") or codex["interface"].get("default_prompt"))
        self.assertFalse(codex.get("hooks"), "the Codex manifest schema does not accept hooks")
        self.assertNotIn("$schema", codex)
        self.assertEqual(codex["skills"], "./skills/")
        skill = (ROOT / "skills" / "buddy" / "SKILL.md").read_text()
        self.assertTrue(skill.startswith("---\n"))
        header = skill.split("---", 2)[1]
        self.assertRegex(header, r"(?m)^name:\s*\S+")
        self.assertRegex(header, r"(?m)^description:\s*\S+")


class StagedMarketplaceTests(unittest.TestCase):
    """A staged plugin directory is a marketplace root of its own."""

    def test_staging_carries_the_catalog_and_a_resolvable_entry(self):
        with tempfile.TemporaryDirectory(prefix="buddy-first-install-") as directory:
            destination = Path(directory) / "hey-my-buddy"
            staged = load_stage_plugin().stage(ROOT, destination)
            catalog_path = staged / ".agents" / "plugins" / "marketplace.json"
            self.assertTrue(catalog_path.is_file(), "the staged tree must carry the catalog")
            catalog = load_json(catalog_path)
            self.assertEqual(catalog, load_json(CATALOG))
            self.assertEqual(resolved_entry(staged, catalog), staged.resolve())
            self.assertTrue((staged / ".codex-plugin" / "plugin.json").is_file())
            refreshed = load_json(staged / "plugin.json")
            codex = load_json(staged / ".codex-plugin" / "plugin.json")
            self.assertEqual(refreshed["version"], codex["version"])
            self.assertEqual(refreshed["name"], codex["name"])
            for unsupported in ("tests", "node_modules", ".venv", "apps"):
                self.assertFalse((staged / unsupported).exists(), unsupported)


class DocumentationTests(unittest.TestCase):
    """Both READMEs and the owning reference describe the same first-install paths."""

    def test_operations_document_local_git_and_staged_install_paths(self):
        text = OPERATIONS.read_text()
        # The runtime section owns the private C-Two profile that the install enables.
        self.assertIn("buddy.rpc_config", text)
        self.assertIn("## Installation", text)
        self.assertIn("codex plugin marketplace add Dsssyc/hey-my-buddy --ref main", text)
        self.assertIn("codex plugin marketplace add /abs/path/to/hey-my-buddy", text)
        self.assertIn("codex plugin marketplace add /path/to/plugins/hey-my-buddy", text)
        self.assertIn("codex plugin add hey-my-buddy@hey-my-buddy", text)
        self.assertIn(".agents/plugins/marketplace.json", text)
        self.assertIn("codex plugin marketplace upgrade", text)
        self.assertIn("--sparse", text)
        self.assertIn("personal marketplace", text)
        self.assertIn("no public registry listing", text)

    def test_readmes_keep_first_install_parity(self):
        for name in ("README.md", "README.zh-CN.md"):
            text = (ROOT / name).read_text()
            self.assertIn("codex plugin marketplace add", text, name)
            self.assertIn("codex plugin add hey-my-buddy@hey-my-buddy", text, name)
            self.assertIn("docs/reference/operations.md#installation", text, name)
            self.assertNotIn("your-marketplace", text, f"{name} must not require a developer's marketplace name")


def codex_binary() -> str | None:
    binary = shutil.which("codex")
    if not binary:
        return None
    probe = subprocess.run([binary, "plugin", "--help"], capture_output=True, text=True, timeout=120)
    return binary if probe.returncode == 0 else None


class CodexCliMarketplaceTests(unittest.TestCase):
    """The installed CLI accepts the shipped catalog without touching global config."""

    def setUp(self):
        self.binary = codex_binary()
        if not self.binary:
            self.skipTest("the Codex CLI plugin surface is required for this check")
        self.work = Path(tempfile.mkdtemp(prefix="buddy-codex-cli-"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.home = self.work / "codex-home"
        self.home.mkdir()
        self.global_config = Path.home() / ".codex" / "config.toml"
        self.before = self.global_config.read_bytes() if self.global_config.exists() else None

    def codex(self, *arguments: str) -> tuple[int, str]:
        environment = {**os.environ, "CODEX_HOME": str(self.home)}
        completed = subprocess.run(
            [self.binary, *arguments], env=environment, capture_output=True, text=True, timeout=180
        )
        # Successful --json data belongs to stdout. Codex can also report a
        # harmless private-TMPDIR PATH-alias warning on stderr; it is not JSON.
        output = completed.stdout if completed.returncode == 0 else completed.stdout + completed.stderr
        return completed.returncode, output

    def test_private_codex_home_resolves_the_repository_catalog(self):
        code, output = self.codex("plugin", "marketplace", "add", str(ROOT), "--json")
        self.assertEqual(code, 0, output)
        added = json.loads(output[output.index("{"):])
        self.assertEqual(added["marketplaceName"], PLUGIN_NAME)
        self.assertEqual(Path(added["installedRoot"]).resolve(), ROOT.resolve())

        code, output = self.codex("plugin", "marketplace", "list", "--json")
        self.assertEqual(code, 0, output)
        names = {entry["name"] for entry in json.loads(output[output.index("{"):])["marketplaces"]}
        self.assertIn(PLUGIN_NAME, names)

        code, output = self.codex("plugin", "list", "--available", "--json", "--marketplace", PLUGIN_NAME)
        self.assertEqual(code, 0, output)
        available = json.loads(output[output.index("{"):])["available"]
        entry = next(item for item in available if item["name"] == PLUGIN_NAME)
        self.assertEqual(entry["pluginId"], f"{PLUGIN_NAME}@{PLUGIN_NAME}")
        self.assertEqual(entry["version"], load_json(ROOT / ".codex-plugin" / "plugin.json")["version"])
        self.assertEqual(entry["installPolicy"], "AVAILABLE")
        self.assertEqual(Path(entry["source"]["path"]).resolve(), ROOT.resolve())
        # Isolation proof: the private home owns the registration and the operator's
        # global configuration is byte-identical.
        self.assertTrue((self.home / "config.toml").exists(), "the private CODEX_HOME must hold the registration")
        after = self.global_config.read_bytes() if self.global_config.exists() else None
        self.assertEqual(after, self.before, "a test must never write the operator's global Codex config")

    def test_staged_marketplace_installs_into_a_private_cache(self):
        staged = load_stage_plugin().stage(ROOT, self.work / PLUGIN_NAME)
        code, output = self.codex("plugin", "marketplace", "add", str(staged), "--json")
        self.assertEqual(code, 0, output)
        code, output = self.codex("plugin", "add", f"{PLUGIN_NAME}@{PLUGIN_NAME}", "--json")
        self.assertEqual(code, 0, output)
        installed = json.loads(output[output.index("{"):])
        self.assertEqual(installed["pluginId"], f"{PLUGIN_NAME}@{PLUGIN_NAME}")
        self.assertEqual(installed["version"], load_json(staged / ".codex-plugin" / "plugin.json")["version"])
        cached = Path(installed["installedPath"])
        self.assertTrue((cached / ".codex-plugin" / "plugin.json").is_file())
        self.assertTrue((cached / "skills" / "buddy" / "SKILL.md").is_file())
        self.assertTrue((cached / "bin" / "buddy").is_file())
        code, output = self.codex("plugin", "list", "--json")
        self.assertEqual(code, 0, output)
        entry = json.loads(output[output.index("{"):])["installed"][0]
        self.assertTrue(entry["installed"])
        self.assertTrue(entry["enabled"])
        after = self.global_config.read_bytes() if self.global_config.exists() else None
        self.assertEqual(after, self.before, "a test must never write the operator's global Codex config")


if __name__ == "__main__":  # pragma: no cover - manual focus
    unittest.main()
