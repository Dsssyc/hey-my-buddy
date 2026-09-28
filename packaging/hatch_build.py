"""Embed the assembled skill in a release wheel without touching the source tree."""
from __future__ import annotations

import shutil
import sys
import tempfile
import re
import types
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        # A stable runtime uses `uv sync` in editable mode. Never nest a second
        # distribution inside it or the next sync would package recursively.
        if self.target_name != "wheel" or version == "editable":
            return
        root = Path(self.root)
        sys.path.insert(0, str(root / "src"))
        # assemble only needs the frozen contract string. Importing contracts
        # would initialize the native C-Two transport inside the build backend.
        contract = re.search(r'^CONTRACT_VERSION = "([^"]+)"',
                             (root / "src/buddy/contracts.py").read_text(), re.MULTILINE)
        if contract is None:
            raise ValueError("Missing frozen contract version")
        marker = types.ModuleType("buddy.contracts")
        marker.CONTRACT_VERSION = contract.group(1)
        previous = sys.modules.get("buddy.contracts")
        sys.modules["buddy.contracts"] = marker
        try:
            from buddy.skill_package import assemble

            self._staging = Path(tempfile.mkdtemp(prefix="buddy-wheel-"))
            skill = self._staging / "buddy"
            try:
                assemble(root, skill)
            except Exception:
                shutil.rmtree(self._staging)
                raise
            for file in skill.rglob("*"):
                if file.is_file():
                    target = "buddy/_distribution/" + file.relative_to(skill).as_posix()
                    build_data["force_include"][str(file)] = target
        finally:
            if previous is None:
                sys.modules.pop("buddy.contracts", None)
            else:
                sys.modules["buddy.contracts"] = previous
            sys.path.remove(str(root / "src"))

    def finalize(self, version: str, build_data: dict, artifact: str) -> None:
        if hasattr(self, "_staging"):
            shutil.rmtree(self._staging)
