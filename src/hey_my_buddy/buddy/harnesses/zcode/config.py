"""Native ZCode paths and secret-free provider classification."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .protocol import NativeError

DEFAULT_CLI = Path("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs")


def cli_command(environment: dict | None = None) -> list[str]:
    env = os.environ if environment is None else environment
    if not (os.environ.get("BUDDY_DEV_SOURCE") == "1" and env.get("BUDDY_ZCODE_CLI")):
        from ..runtime_selection import command_for
        from ....errors import BoardError
        try:
            return command_for("zcode", env)
        except BoardError as error:
            raise NativeError("harness-unavailable", error.message) from None
    value = env.get("BUDDY_ZCODE_CLI") or shutil.which("zcode", path=env.get("PATH")) or str(DEFAULT_CLI)
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise NativeError("harness-unavailable", "ZCode CLI is missing; configure BUDDY_ZCODE_CLI")
    if path.suffix in (".js", ".cjs", ".mjs"):
        node = env.get("BUDDY_NODE") or shutil.which("node", path=env.get("PATH"))
        if not node:
            raise NativeError("harness-unavailable", "Node.js is required for the installed ZCode bundle")
        return [node, str(path)]
    if not os.access(path, os.X_OK):
        raise NativeError("harness-unavailable", "the configured ZCode CLI is not executable")
    return [str(path)]


def provider_paths(environment: dict, command: list[str]) -> tuple[Path, Path]:
    """Required built-in location and optional personal billing location."""
    env = environment
    cli = Path(command[-1])
    candidates = [cli.parent.parent / "config/provider/zcode-builtin.json", cli.parent / "provider/zcode-builtin.json"]
    builtin = next((p for p in candidates if p.is_file()), None)
    if builtin is None:
        raise NativeError("harness-unavailable", "ZCode's installed built-in provider configuration file is missing")
    personal = Path(env.get("ZCODE_DATA_BASE_DIR") or Path.home()) / ".zcode/v2/provider_config.json"
    return builtin, personal


def provider_access_types(builtin: Path, personal: Path) -> dict[str, str]:
    """Project only provider/template identities and billing access types.

    These optional credential-adjacent reads never determine availability or
    model identity. Unreadable or unrecognized metadata means unknown billing.
    Neither credentials nor arbitrary access-type strings leave this boundary.
    """
    missing_access = object()
    try:
        b = json.loads(builtin.read_text())["config"]["providerConfigRules"]
        p = json.loads(personal.read_text())["config"]["providerConfigRules"]
        templates = {r["templateId"]: r.get("config", {}).get("access", {})
                     for r in b.get("templateRules", [])}
        rules = {r["providerId"]: (r.get("templateId"), r.get("config", {}).get("access", missing_access))
                 for r in b.get("providerRules", [])}
        for r in p.get("providerRules", []):
            template, access = rules.get(r["providerId"], (None, missing_access))
            rules[r["providerId"]] = (r.get("templateId", template), r.get("config", {}).get("access", access))
        result = {}
        for name, (template, access) in rules.items():
            if access is missing_access:
                access = templates.get(template, {})
            kind = access.get("type") if isinstance(access, dict) else None
            result[name] = kind if kind in ("api-key", "zhipu-coding-plan-api-key") else "unknown"
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def load_runtime_preferences(environment: dict) -> dict:
    """Read only the two optional app settings the CLI asks its host to supply."""
    path = Path(environment.get("ZCODE_DATA_BASE_DIR") or Path.home()) / ".zcode/v2/setting.json"
    try:
        document = json.loads(path.read_text())
    except (OSError, ValueError):
        document = None
    settings = document if isinstance(document, dict) else {}
    result = {}
    for key, default in (("memoryEnabled", False), ("nativeSearchEnhancementsEnabled", True)):
        value = settings.get(key)
        result[key] = {"value": value if type(value) is bool else default,
                       "source": "user-settings" if type(value) is bool else "fallback"}
    return result
