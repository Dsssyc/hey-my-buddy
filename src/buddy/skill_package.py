"""Build the shared, self-contained ``buddy`` Agent Skill from a source checkout.

The skill is the only distribution (ADR-015). Its directory follows the Agent Skills
layout and carries everything a Host needs, with no repository or plugin path:

    buddy/
      SKILL.md              instructions; links point into references/
      skill.json            version marker used to skip a repeated install
      scripts/buddy         the CLI launcher
      references/*.md       the current operational references
      package/              the uv project the stable runtime is materialized from

Test suites, virtual environments, the React source and scratch content are
excluded by construction, and the inventory is verified before publication.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import tomllib

from .errors import BoardError
from .runtime import iter_assets

SKILL_NAME = "buddy"
UNSUPPORTED_PARTS = ("tests", "node_modules", ".venv", "__pycache__", ".git", "tmp", ".dsh-skill-build", "apps")
IGNORED_COPY = ("__pycache__", "*.pyc", "*.pyo")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def project_version(source: Path) -> str:
    return tomllib.loads((source / "pyproject.toml").read_text())["project"]["version"]


def source_commit(source: Path) -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        metadata = source / "src/buddy/build-info.json"
        return json.loads(metadata.read_text()).get("sourceCommit") if metadata.exists() else None


def _rewrite(text: str, local: callable) -> str:
    """Keep links that resolve inside the skill; reduce the rest to their label."""
    def replace(match: re.Match) -> str:
        label, target = match.group(1), match.group(2)
        if re.match(r"[a-z]+://", target) or target.startswith("#"):
            return match.group(0)
        rewritten = local(target)
        return f"[{label}]({rewritten})" if rewritten else label
    return _LINK.sub(replace, text)


def _skill_link(target: str) -> str | None:
    prefix = "../../docs/reference/"
    return "references/" + target[len(prefix):] if target.startswith(prefix) else None


def _reference_link(target: str) -> str | None:
    return target if "/" not in target.split("#", 1)[0] else None


def _with_version(text: str, version: str) -> str:
    if not text.startswith("---\n"):
        raise BoardError("SKILL_INVALID", "SKILL.md has no frontmatter")
    head, body = text[4:].split("\n---\n", 1)
    head = "\n".join(line for line in head.splitlines() if not line.startswith(("metadata:", "  version:")))
    return f"---\n{head}\nmetadata:\n  version: \"{version}\"\n---\n{body}"


def assert_supported_inventory(skill: Path) -> None:
    for path in sorted(skill.rglob("*")):
        parts = path.relative_to(skill).parts
        unsupported = next((part for part in parts if part in UNSUPPORTED_PARTS), None)
        if unsupported:
            raise BoardError("SKILL_INVALID", f"Refusing to package unsupported content: {'/'.join(parts)}")
        if path.suffix in (".pyc", ".pyo"):
            raise BoardError("SKILL_INVALID", f"Refusing to package a compiled Python file: {'/'.join(parts)}")


def assemble(source: Path, skill: Path) -> dict:
    """Write the skill into the empty directory *skill* and return its marker."""
    from .contracts import CONTRACT_VERSION

    source = Path(source).resolve()
    version = project_version(source)
    skill.mkdir(parents=True, exist_ok=True)
    text = (source / "skills/buddy/SKILL.md").read_text()
    (skill / "SKILL.md").write_text(_with_version(_rewrite(text, _skill_link), version))
    (skill / "scripts").mkdir()
    shutil.copy2(source / "skills/buddy/scripts/buddy", skill / "scripts/buddy")
    (skill / "scripts/buddy").chmod(0o755)
    (skill / "references").mkdir()
    for reference in sorted((source / "docs/reference").glob("*.md")):
        (skill / "references" / reference.name).write_text(_rewrite(reference.read_text(), _reference_link))
    package = skill / "package"
    for relative, origin in iter_assets(source):
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, target)
    shutil.copy2(source / "LICENSE", package / "LICENSE")
    commit = source_commit(source)
    (package / "src/buddy/build-info.json").write_text(json.dumps({"sourceCommit": commit}) + "\n")
    marker = {"name": SKILL_NAME, "version": version, "contract": CONTRACT_VERSION, "sourceCommit": commit}
    (skill / "skill.json").write_text(json.dumps(marker, indent=2) + "\n")
    assert_supported_inventory(skill)
    return marker


def build(source: Path, destination: Path) -> Path:
    """Assemble the skill next to *destination* and publish it by rename."""
    destination = Path(destination).expanduser().absolute()
    if destination.name != SKILL_NAME:
        raise BoardError("SKILL_INVALID", "The skill directory must be named buddy")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".buddy-build-", dir=destination.parent))
    try:
        assemble(source, staged / SKILL_NAME)
        publish(staged / SKILL_NAME, destination)
        return destination
    finally:
        shutil.rmtree(staged, ignore_errors=True)


def publish(prepared: Path, destination: Path) -> None:
    """Replace *destination* with the prepared sibling; the old copy is removed after."""
    previous = None
    if destination.is_symlink():
        raise BoardError("SKILL_TARGET_CONFLICT", f"{destination} is a symbolic link; remove it before installing")
    if destination.exists():
        previous = destination.with_name(f".{destination.name}-previous-{time.time_ns()}")
        destination.rename(previous)
    prepared.rename(destination)
    if previous is not None:
        shutil.rmtree(previous, ignore_errors=True)
