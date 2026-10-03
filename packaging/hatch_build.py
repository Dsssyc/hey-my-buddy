"""Embed the assembled skill in a release wheel without touching the source tree.

The sdist additionally carries ``src/hey_my_buddy/build-info.json`` with the source commit,
so a wheel built later from that sdist (where Git and the checkout are absent) keeps
the same ``skill.json`` ``sourceCommit`` instead of reporting null.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import types
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name == "sdist":
            build_data["force_include"].update(self._build_info(self._staging()))
            return
        # A stable runtime uses `uv sync` in editable mode. Never nest a second
        # distribution inside it or the next sync would package recursively.
        if self.target_name != "wheel" or version == "editable":
            return
        root = Path(self.root)
        sys.path.insert(0, str(root / "src"))
        # assemble only needs the frozen contract string. Importing contracts
        # would initialize the native C-Two transport inside the build backend.
        contract = re.search(r'^CONTRACT_VERSION = "([^"]+)"',
                             (root / "src/hey_my_buddy/protocol/contracts.py").read_text(), re.MULTILINE)
        if contract is None:
            raise ValueError("Missing frozen contract version")
        marker = types.ModuleType("hey_my_buddy.protocol.contracts")
        marker.CONTRACT_VERSION = contract.group(1)
        previous = sys.modules.get("hey_my_buddy.protocol.contracts")
        sys.modules["hey_my_buddy.protocol.contracts"] = marker
        try:
            from hey_my_buddy.install.skill_package import assemble

            staging = self._staging()
            skill = staging / "buddy"
            try:
                assemble(root, skill)
            except Exception:
                shutil.rmtree(staging)
                raise
            for file in skill.rglob("*"):
                if file.is_file():
                    target = "hey_my_buddy/_distribution/" + file.relative_to(skill).as_posix()
                    build_data["force_include"][str(file)] = target
        finally:
            if previous is None:
                sys.modules.pop("hey_my_buddy.protocol.contracts", None)
            else:
                sys.modules["hey_my_buddy.protocol.contracts"] = previous
            sys.path.remove(str(root / "src"))

    def _build_info(self, staging: Path) -> dict[str, str]:
        """Map a staged ``src/hey_my_buddy/build-info.json`` into the sdist."""
        root = Path(self.root)
        sys.path.insert(0, str(root / "src"))
        try:
            from hey_my_buddy.install.skill_package import source_commit

            commit = source_commit(root)
        finally:
            sys.path.remove(str(root / "src"))
        metadata = root / "src/hey_my_buddy/build-info.json"
        try:
            # Building again from an already-extracted sdist: its include list
            # already carries the identical file, so do not add a duplicate.
            if json.loads(metadata.read_text()).get("sourceCommit") == commit:
                return {}
        except (OSError, ValueError):
            pass
        path = staging / "build-info.json"
        path.write_text(json.dumps({"sourceCommit": commit}) + "\n")
        return {str(path): "src/hey_my_buddy/build-info.json"}

    def _staging(self) -> Path:
        """One private staging directory, removed by ``finalize`` after the build."""
        paths = getattr(self, "_staging_paths", None)
        if paths is None:
            paths = []
            self._staging_paths = paths
        directory = Path(tempfile.mkdtemp(prefix="buddy-build-"))
        paths.append(directory)
        return directory

    def finalize(self, version: str, build_data: dict, artifact: str) -> None:
        for path in getattr(self, "_staging_paths", ()):
            shutil.rmtree(path, ignore_errors=True)
        self._staging_paths = []
