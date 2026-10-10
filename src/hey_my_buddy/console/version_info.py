"""Small, read-only version projection for the settings page."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

from ..blackboard.store.db import SCHEMA_VERSION
from ..errors import BoardError
from ..install import launcher, runtime
from ..install.skill_package import project_version
from ..protocol.contracts import CONTRACT_VERSION


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _software_version(root: Path) -> str | None:
    try:
        return _text(project_version(root))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _constant(path: Path, name: str) -> object:
    """Read one installed declaration without importing or executing that package."""
    try:
        for statement in ast.parse(path.read_text()).body:
            if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in statement.targets
            ):
                return ast.literal_eval(statement.value)
    except (OSError, ValueError, SyntaxError, TypeError):
        pass
    return None


def _build_commit(root: Path) -> str | None:
    try:
        return _text(json.loads((root / "src/hey_my_buddy/build-info.json").read_text()).get("sourceCommit"))
    except (OSError, ValueError, AttributeError):
        return None


def version_info(state: Path) -> dict:
    """Separate the executing service from an installed READY runtime, if present.

    Runtime identity and installation metadata use the existing resolver. Missing
    build metadata stays unknown; this read never asks Git for the checkout HEAD.
    The resolver's paths, resources and private records do not cross this boundary.
    """
    identity = runtime.resolve_runtime()
    root = runtime.project_root()
    in_use = identity.get("state") == "READY" and identity.get("inUse") is True
    running = {
        "mode": "runtime" if in_use else "source",
        "softwareVersion": _software_version(root),
        "contractVersion": CONTRACT_VERSION,
        "schemaVersion": SCHEMA_VERSION,
        "sourceCommit": _text(identity.get("sourceCommit")) if in_use else _build_commit(root),
        "installedAt": _text(identity.get("installedAt")) if in_use else None,
    }
    # A source checkout's content ID cannot identify an older active install.
    # Follow the existing validated state pointer, never scan other READY dirs.
    installation = identity
    if not in_use and not os.environ.get("BUDDY_RUNTIME"):
        try:
            selected = launcher.selected_runtime(state, allow_overrides=False)
            installation = runtime.read_ready(selected) if selected is not None else {}
        except (BoardError, OSError, ValueError, TypeError, AttributeError):
            installation = {}
    installed = None
    if installation.get("state") == "READY":
        installed_root = Path(installation["runtimeDir"])
        package = installed_root / "src/hey_my_buddy"
        schema = _constant(package / "blackboard/store/db.py", "SCHEMA_VERSION")
        installed = {
            "softwareVersion": _software_version(installed_root),
            "contractVersion": _text(_constant(package / "protocol/contracts.py", "CONTRACT_VERSION")),
            "schemaVersion": schema if type(schema) is int else None,
            "sourceCommit": _text(installation.get("sourceCommit")),
            "installedAt": _text(installation.get("installedAt")),
        }
    return {"running": running, "installed": installed}
