"""Tracked files never name the machine they were written on.

Records, fixtures and saved evidence are public. A home directory or the layout of a
project directory identifies one person's machine, so they are written as ``~`` or a
placeholder. The check below runs on whichever machine runs the suite and refuses a
tracked file that carries that machine's own paths.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def personal_markers(home: Path, main_checkout: Path | None) -> dict[str, str]:
    """The strings that identify one machine: its home and its project directory."""
    markers: dict[str, str] = {}
    # A bare "/root" or "/" is too short to be distinguishing.
    if len(home.parts) >= 3:
        markers[str(home)] = "the absolute home directory"
    if main_checkout is None:
        return markers
    try:
        relative = main_checkout.parent.relative_to(home)
    except ValueError:
        return markers
    # One level ("src/") is generic, and a hidden tool directory is not a personal
    # layout; two visible levels are.
    if len(relative.parts) >= 2 and not relative.parts[0].startswith("."):
        for separator in ("/", "\\"):
            markers[separator.join(relative.parts[:2]) + separator] = "the project directory layout"
    return markers


def offending_files(root: Path, names: list[str], markers: dict[str, str]) -> list[str]:
    """Text files below ``root`` that contain a marker, as ``name: meaning`` lines."""
    offenders: list[str] = []
    for name in names:
        try:
            data = (root / name).read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        for marker, meaning in markers.items():
            if marker.encode("utf-8", errors="surrogateescape") in data:
                offenders.append(f"{name}: {meaning}")
    return sorted(set(offenders))


def _git(*arguments: str) -> str | None:
    try:
        completed = subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="surrogateescape")


class PersonalMarkerTests(unittest.TestCase):
    def test_home_and_two_visible_project_levels_are_markers(self):
        markers = personal_markers(Path("/Users/alice"), Path("/Users/alice/Desktop/code/project"))
        self.assertEqual(
            markers,
            {
                "/Users/alice": "the absolute home directory",
                "Desktop/code/": "the project directory layout",
                "Desktop\\code\\": "the project directory layout",
            },
        )

    def test_generic_hidden_or_foreign_locations_add_no_layout_marker(self):
        home = Path("/Users/alice")
        for checkout in (
            Path("/Users/alice/project"),
            Path("/Users/alice/src/project"),
            Path("/Users/alice/.tool/worktrees/name/project"),
            Path("/srv/build/checkouts/project"),
            None,
        ):
            with self.subTest(checkout=checkout):
                self.assertEqual(personal_markers(home, checkout), {"/Users/alice": "the absolute home directory"})

    def test_a_short_home_is_not_a_marker(self):
        self.assertEqual(personal_markers(Path("/root"), None), {})

    def test_only_text_files_carrying_a_marker_are_reported(self):
        directory = Path(tempfile.mkdtemp(prefix="hygiene-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        (directory / "clean.md").write_text("The archive is under ~/.local/share/example.\n")
        (directory / "leaky.md").write_text("The archive is under /Users/alice/.local/share/example.\n")
        (directory / "layout.ts").write_text('const path = "~/Desktop/code/project";\n')
        (directory / "binary.bin").write_bytes(b"\0\1/Users/alice/Desktop/code/")
        names = ["clean.md", "leaky.md", "layout.ts", "binary.bin", "missing.md"]
        markers = personal_markers(Path("/Users/alice"), Path("/Users/alice/Desktop/code/project"))
        self.assertEqual(
            offending_files(directory, names, markers),
            ["layout.ts: the project directory layout", "leaky.md: the absolute home directory"],
        )


class TrackedFileTests(unittest.TestCase):
    def test_tracked_files_do_not_name_this_machine(self):
        listed = _git("ls-files", "-z")
        if listed is None:
            self.skipTest("not a Git checkout")
        common = _git("rev-parse", "--git-common-dir")
        main_checkout = (ROOT / common.strip()).resolve().parent if common and common.strip() else None
        markers = personal_markers(Path.home(), main_checkout)
        if not markers:
            self.skipTest("this machine has no distinguishing home or project directory")
        offenders = offending_files(ROOT, [name for name in listed.split("\0") if name], markers)
        self.assertEqual(offenders, [], "write ~ or a placeholder instead of this machine's own paths")


if __name__ == "__main__":
    unittest.main()
