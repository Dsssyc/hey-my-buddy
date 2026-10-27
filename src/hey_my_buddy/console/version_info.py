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


def _constant(path: Path, name: str) -> object:
    """Read one installed declaration without importing or executing that package."""
    for statement in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in statement.targets
        ):
            return ast.literal_eval(statement.value)
    raise ValueError(f"Installed declaration {name} is missing")


def _build_commit(root: Path) -> str | None:
    try:
        return json.loads((root / "src/hey_my_buddy/build-info.json").read_text(encoding="utf-8"))["sourceCommit"]
    except FileNotFoundError:
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
        "softwareVersion": project_version(root),
        "contractVersion": CONTRACT_VERSION,
        "schemaVersion": SCHEMA_VERSION,
        "sourceCommit": identity["sourceCommit"] if in_use else _build_commit(root),
        "installedAt": identity["installedAt"] if in_use else None,
    }
    # A source checkout's content ID cannot identify an older active install.
    # Follow the existing validated state pointer, never scan other READY dirs.
    installation = identity
    if not in_use and not os.environ.get("HMB_RUNTIME"):
        try:
            selected = launcher.selected_runtime(state, allow_overrides=False)
            installation = runtime.read_ready(selected) if selected is not None else {}
        except BoardError as error:
            if error.code not in ("DEV_SOURCE_REQUIRED", "ACTIVE_RUNTIME_UNAVAILABLE"):
                raise
            installation = {}
    installed = None
    if installation.get("state") == "READY":
        installed_root = Path(installation["runtimeDir"])
        package = installed_root / "src/hey_my_buddy"
        schema = _constant(package / "blackboard/store/db.py", "SCHEMA_VERSION")
        installed = {
            "softwareVersion": project_version(installed_root),
            "contractVersion": _constant(package / "protocol/contracts.py", "CONTRACT_VERSION"),
            "schemaVersion": schema,
            "sourceCommit": installation["sourceCommit"],
            "installedAt": installation["installedAt"],
        }
    return {"running": running, "installed": installed}
