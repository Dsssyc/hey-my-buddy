"""Create a discoverable, model-free test command with the current Python."""
from __future__ import annotations

from pathlib import Path
import sys

from distlib.scripts import ScriptMaker


def make_fake_executable(directory: Path, name: str, *, source: str | Path) -> Path:
    """Install a private console script; source supplies all CLI behavior.

    distlib writes a native console launcher on Windows and an executable script
    on POSIX. A file source runs under its original path so __file__ and sibling
    imports keep their fixture semantics.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    source_dir = directory / "_script_sources"
    source_dir.mkdir(exist_ok=True)
    if isinstance(source, Path):
        fixture = source.resolve()
        body = ("import runpy, sys\n"
                f"sys.path.insert(0, {str(fixture.parent)!r})\n"
                f"runpy.run_path({str(fixture)!r}, run_name='__main__')\n")
    else:
        body = source
    (source_dir / name).write_text("#!python\n" + body, encoding="utf-8")
    maker = ScriptMaker(str(source_dir), str(directory))
    maker.executable = sys.executable
    maker.variants = {""}
    made = [Path(path) for path in maker.make(name)]
    expected = directory / (name + (".exe" if sys.platform == "win32" else ""))
    if expected not in made or not expected.is_file():
        raise AssertionError(f"distlib did not create {expected}")
    return expected
