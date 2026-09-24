"""Native ZCode paths and secret-free provider classification."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .zcode_protocol import NativeError

SUPPORTED_ACCESS = {"api-key", "zhipu-coding-plan-api-key"}
DEFAULT_CLI = Path("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs")


def cli_command(environment: dict | None = None) -> list[str]:
    env = os.environ if environment is None else environment
    value = env.get("BUDDY_ZCODE_CLI") or shutil.which("zcode", path=env.get("PATH")) or str(DEFAULT_CLI)
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise NativeError("adapter-unavailable", "ZCode CLI is missing; configure BUDDY_ZCODE_CLI")
    if path.suffix in (".js", ".cjs", ".mjs"):
        node = env.get("BUDDY_NODE") or shutil.which("node", path=env.get("PATH"))
        if not node:
            raise NativeError("adapter-unavailable", "Node.js is required for the installed ZCode bundle")
        return [node, str(path)]
    if not os.access(path, os.X_OK):
        raise NativeError("adapter-unavailable", "the configured ZCode CLI is not executable")
    return [str(path)]


def provider_paths(environment: dict | None = None) -> tuple[Path, Path]:
    env = os.environ if environment is None else environment
    command = cli_command(env)
    cli = Path(command[-1])
    candidates = [cli.parent.parent / "config/provider/zcode-builtin.json", cli.parent / "provider/zcode-builtin.json"]
    builtin = Path(env["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"]).expanduser() if env.get("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE") else next((p for p in candidates if p.is_file()), candidates[0])
    personal = Path(env["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"]).expanduser() if env.get("ZCODE_PERSONAL_PROVIDER_CONFIG_FILE") else Path(env.get("ZCODE_DATA_BASE_DIR") or Path.home()) / ".zcode/v2/provider_config.json"
    if not builtin.is_file() or not personal.is_file():
        raise NativeError("adapter-unavailable", "ZCode built-in and personal provider configuration files are required")
    return builtin.resolve(), personal.resolve()


def provider_access_types(builtin: Path, personal: Path) -> dict[str, str]:
    """Inspect only access type; never return provider credentials or requestAuth."""
    try:
        b = json.loads(builtin.read_text())["config"]["providerConfigRules"]
        p = json.loads(personal.read_text())["config"]["providerConfigRules"]
        templates = {r["templateId"]: r.get("config", {}) for r in b.get("templateRules", [])}
        rules = {r["providerId"]: r for r in b.get("providerRules", [])}
        for r in p.get("providerRules", []):
            old = rules.get(r["providerId"], {})
            rules[r["providerId"]] = {**old, **r, "config": {**old.get("config", {}), **r.get("config", {})}}
        result = {}
        for name, r in rules.items():
            inherited = templates.get(r.get("templateId"), {}).get("access", {}) or {}
            access = r.get("config", {}).get("access", inherited)
            result[name] = access.get("type", "unknown") if isinstance(access, dict) else "unknown"
        return result
    except (OSError, ValueError, KeyError, TypeError):
        raise NativeError("invalid-provider-config", "ZCode provider configuration could not be inspected") from None


def snapshot_provider_files(directory: Path, environment: dict) -> tuple[dict, dict[str, str]]:
    builtin, personal = provider_paths(environment)
    access = provider_access_types(builtin, personal)
    result = dict(environment)
    for source, name, variable in ((builtin, "builtin-provider.json", "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"),
                                   (personal, "personal-provider.json", "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE")):
        # ZCode can normalize its provider files while loading them. A private
        # snapshot of these two files prevents writes to the source preferences;
        # OAuth stores and the rest of ~/.zcode are never copied.
        target = directory / name
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(source.read_bytes())
        result[variable] = str(target)
    result.pop("ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE", None)
    return result, access
