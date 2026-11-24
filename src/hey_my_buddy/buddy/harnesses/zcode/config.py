"""Native ZCode paths and secret-free provider classification."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import sys
from pathlib import Path
from urllib.parse import urlparse

from .protocol import NativeError

_DEFAULT_PRODUCTION_ORIGIN = "https://zcode.z.ai"
_DEFAULT_TEST_ORIGIN = "https://zcode.chatglm.site"


def launch(environment: dict | None = None) -> tuple[list[str], dict]:
    """Resolve the native argv and its environment from one selected entry."""
    from ..runtime_selection import launch_with_environment
    from ..discovery import EntryUnavailable
    from ....errors import BoardError

    try:
        return launch_with_environment("zcode", environment)
    except BoardError as error:
        raise NativeError("harness-unavailable", error.message) from None
    except EntryUnavailable as error:
        failure = NativeError(error.code, str(error))
        failure.reason = error.reason
        raise failure from None


def cli_command(environment: dict | None = None) -> list[str]:
    return launch(environment)[0]


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


def _client_platform() -> str:
    os_name = "windows" if sys.platform == "win32" else sys.platform
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        arch = "aarch64"
    elif machine in ("amd64", "x86_64", "x64"):
        arch = "x86_64"
    else:
        arch = machine
    return f"{os_name}-{arch}"


def _endpoint_dir(environment: dict) -> str:
    is_test = (environment.get("ZCODE_ENV") or "").strip().lower() == "test"
    env_origin = (
        environment.get("ZCODE_BASE_URL")
        or environment.get("ZCODE_ENDPOINT_ORIGIN")
        or environment.get("ZCODE_TEST_BASE_URL" if is_test else "ZCODE_PRODUCTION_BASE_URL")
        or (_DEFAULT_TEST_ORIGIN if is_test else _DEFAULT_PRODUCTION_ORIGIN)
    ).strip()
    parsed = urlparse(env_origin)
    if parsed.scheme in ("http", "https") and parsed.hostname:
        port = parsed.port
        default_port = 443 if parsed.scheme == "https" else 80
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        origin = f"{parsed.scheme}://{host}" if port in (None, default_port) else f"{parsed.scheme}://{host}:{port}"
    else:
        origin = _DEFAULT_TEST_ORIGIN if is_test else _DEFAULT_PRODUCTION_ORIGIN
    return "endpoint-" + hashlib.sha256(origin.encode("utf-8")).hexdigest()[:32]


def _installed_app_version(environment: dict, command: list[str]) -> str | None:
    pinned = (environment.get("ZCODE_APP_VERSION") or "").strip()
    if pinned and pinned not in (".", "..") and "/" not in pinned and "\\" not in pinned:
        return pinned
    if not command:
        return None
    cli = Path(command[-1])
    plist_path = cli.parent.parent.parent / "Info.plist"
    if not plist_path.is_file():
        return None
    try:
        info = plistlib.loads(plist_path.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    version = info.get("CFBundleShortVersionString") if isinstance(info, dict) else None
    if isinstance(version, str):
        version = version.strip()
        if version and version not in (".", "..") and "/" not in version and "\\" not in version:
            return version
    return None


def _builtin_release(path: Path) -> tuple[int, int] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or not isinstance(document.get("config"), dict):
        return None
    schema_version = document.get("schemaVersion")
    revision = document.get("revision")
    if type(schema_version) is not int or type(revision) is not int:
        return None
    return schema_version, revision


def active_builtin_provider_path(environment: dict, command: list[str], bundled: Path) -> Path:
    """Prefer ZCode's newer runtime-synced provider config over its bundled copy."""
    bundled_release = _builtin_release(bundled)
    if bundled_release is None:
        return bundled
    bundled_schema, bundled_rev = bundled_release
    platform_dir = Path(environment.get("ZCODE_DATA_BASE_DIR") or Path.home()) / ".zcode/v2/runtime/provider" / _client_platform()
    if not platform_dir.is_dir():
        return bundled
    endpoint = _endpoint_dir(environment)
    app_version = _installed_app_version(environment, command)
    if app_version is not None:
        exact = platform_dir / app_version / endpoint / "zcode-builtin.json"
        if exact.is_file():
            release = _builtin_release(exact)
            if release is not None and release[0] == bundled_schema and release[1] > bundled_rev:
                return exact
    best: tuple[int, int, Path] | None = None
    try:
        entries = list(platform_dir.iterdir())
    except OSError:
        return bundled
    for entry in entries:
        if not entry.is_dir():
            continue
        candidate = entry / endpoint / "zcode-builtin.json"
        if not candidate.is_file():
            continue
        release = _builtin_release(candidate)
        if release is None or release[0] != bundled_schema or release[1] <= bundled_rev:
            continue
        try:
            mtime_ns = candidate.stat().st_mtime_ns
        except OSError:
            continue
        score = (release[1], mtime_ns, candidate)
        if best is None or score[:2] > best[:2]:
            best = score
    return best[2] if best is not None else bundled


def provider_access_types(builtin: Path, personal: Path) -> dict[str, str]:
    """Project only provider/template identities and billing access types.

    These optional credential-adjacent reads never determine availability or
    model identity. Unreadable or unrecognized metadata means unknown billing.
    Neither credentials nor arbitrary access-type strings leave this boundary.
    """
    missing_access = object()
    try:
        b = json.loads(builtin.read_text(encoding="utf-8"))["config"]["providerConfigRules"]
        p = json.loads(personal.read_text(encoding="utf-8"))["config"]["providerConfigRules"]
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
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        document = None
    settings = document if isinstance(document, dict) else {}
    result = {}
    for key, default in (("memoryEnabled", False), ("nativeSearchEnhancementsEnabled", True)):
        value = settings.get(key)
        result[key] = {"value": value if type(value) is bool else default,
                       "source": "user-settings" if type(value) is bool else "fallback"}
    return result
