"""``buddy install``: place the shared skill once and bring the service to it (ADR-015).

The canonical copy lives in ``~/.agents/skills/buddy`` (read by Codex); Claude Code
reaches it through ``~/.claude/skills/buddy``, a symbolic link to that directory. A
second Host running the same version finds the marker and only repairs the link.
When a service is running, the installed skill's own launcher runs ``upgrade`` so the
daily service switches to this package with the usual idle checks and backup.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from . import schemas, skill_package
from .errors import BoardError
from .runtime import project_root
from .transport import get_state_dir

SKILL = skill_package.SKILL_NAME


def agent_skills_home() -> Path:
    return Path(os.environ.get("BUDDY_AGENT_SKILLS_DIR") or Path.home() / ".agents/skills").expanduser().absolute()


def claude_skills_home() -> Path:
    return Path(os.environ.get("BUDDY_CLAUDE_SKILLS_DIR") or Path.home() / ".claude/skills").expanduser().absolute()


def _marker(skill: Path) -> dict | None:
    try:
        value = json.loads((skill / "skill.json").read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("name") == SKILL else None


def _is_buddy_skill(path: Path) -> bool:
    try:
        head = (path / "SKILL.md").read_text().split("\n---\n", 1)[0]
    except OSError:
        return False
    return any(line.strip() == f"name: {SKILL}" for line in head.splitlines())


def packaged_skill() -> Path | None:
    """The installed-format skill this process runs from, if any."""
    project = project_root()
    candidate = project.parent
    if project.name == "package" and _marker(candidate) and (candidate / "scripts/buddy").is_file():
        return candidate
    return None


def _place(source: Path, target: Path, marker: dict) -> str:
    current = _marker(target) if target.is_dir() and not target.is_symlink() else None
    if current and all(current.get(key) == marker.get(key) for key in ("version", "sourceCommit")):
        return "already-current"
    if target.is_symlink() or (target.exists() and not current and not _is_buddy_skill(target)):
        raise BoardError("SKILL_TARGET_CONFLICT", f"{target} exists and is not a buddy skill; move it away first")
    staged = Path(tempfile.mkdtemp(prefix=".buddy-install-", dir=target.parent))
    try:
        shutil.copytree(source, staged / SKILL, symlinks=True)
        skill_package.publish(staged / SKILL, target)
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    return "updated" if current else "installed"


def _link_claude(target: Path) -> dict:
    home = claude_skills_home()
    link = home / SKILL
    if link.is_symlink():
        resolved = Path(os.path.realpath(link))
        if resolved == Path(os.path.realpath(target)):
            return {"path": str(link), "status": "already-linked"}
        if resolved.exists() and not _is_buddy_skill(resolved):
            return {"path": str(link), "status": "conflict", "reason": "links to a different skill; left unchanged"}
        link.unlink()
    elif link.exists():
        return {"path": str(link), "status": "conflict", "reason": "is a separate directory; left unchanged"}
    home.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    return {"path": str(link), "status": "linked"}


def _service(target: Path) -> dict:
    state = get_state_dir()
    if not (state / "control.json").exists():
        return {"action": "none", "reason": "no running service; the next command starts it from the installed runtime pin or this package"}
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"BUDDY_RUNTIME", "BUDDY_RUNTIME_IDENTITY", "BUDDY_PYTHON", "PYTHONPATH",
                                  "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}}
    completed = subprocess.run([str(target / "scripts" / SKILL), "upgrade"], env=environment,
                               capture_output=True, text=True, timeout=3600, check=False)
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise BoardError("SKILL_UPGRADE_FAILED", "The installed launcher returned no upgrade result") from None
    return {"action": "upgrade", "result": result}


def _legacy_plugins() -> list[str]:
    cache = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "plugins/cache"
    return sorted(str(path) for path in cache.glob("*/hey-my-buddy") if path.is_dir()) if cache.is_dir() else []


def install(params: dict) -> dict:
    schemas.reject_unknown(params, set(), "install")
    if os.environ.get("BUDDY_AGENT_CREDENTIAL") or os.environ.get("BUDDY_AGENT_CREDENTIAL_FILE"):
        raise BoardError("UNAUTHORIZED", "A Worker cannot install the shared skill")
    import fcntl  # POSIX-only lock; imported here so read-only commands stay portable.

    home = agent_skills_home()
    home.mkdir(parents=True, exist_ok=True)
    target = home / SKILL
    lock = os.open(home / f".{SKILL}-install.lock", os.O_CREAT | os.O_RDWR, 0o600)
    built = None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        source = packaged_skill()
        if source is None:
            built = Path(tempfile.mkdtemp(prefix=".buddy-source-", dir=home))
            skill_package.assemble(project_root(), built / SKILL)
            source = built / SKILL
        marker = _marker(source)
        if Path(os.path.realpath(source)) == Path(os.path.realpath(target)):
            placement = "already-current"
        else:
            placement = _place(source, target, marker)
        claude = _link_claude(target)
    finally:
        if built is not None:
            shutil.rmtree(built, ignore_errors=True)
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
    result = {"skill": {"path": str(target), "version": marker["version"], "contract": marker["contract"],
                        "placement": placement},
              "claude": claude, "service": _service(target)}
    legacy = _legacy_plugins()
    if legacy:
        result["legacyPlugins"] = legacy
        result["next"] = "Retire the Codex plugin: codex plugin remove hey-my-buddy@<marketplace>"
    return result


def _claude_status(target: Path) -> str:
    link = claude_skills_home() / SKILL
    if link.is_symlink():
        return "linked" if Path(os.path.realpath(link)) == Path(os.path.realpath(target)) else "conflict"
    return "conflict" if link.exists() else "missing"


def paths(params: dict) -> dict:
    """Where the skill, launcher, data and runtime live; reads files only, starts nothing."""
    schemas.reject_unknown(params, set(), "paths")
    from .runtime import runtime_root
    home = agent_skills_home()
    target = home / SKILL
    marker = _marker(target) if target.is_dir() else None
    running = packaged_skill()
    state = get_state_dir()
    pointer = None
    try:
        pointer = json.loads((state / "active-runtime.json").read_text()).get("runtimeDir")
    except (OSError, ValueError, AttributeError):
        pass
    return {
        "skill": {"dir": str(target), "installed": marker is not None,
                  "version": marker.get("version") if marker else None,
                  "launcher": str(target / "scripts" / SKILL)},
        "claude": {"path": str(claude_skills_home() / SKILL), "status": _claude_status(target)},
        "invokedFrom": str(running) if running else str(project_root()),
        "data": {"stateDir": str(state), "board": str(state / "board.sqlite3"),
                 "attempts": str(state / "attempts"), "workspaces": str(state / "workspaces"),
                 "backup": str(state / "backups" / "current"), "serviceLog": str(state / "control.log")},
        "runtime": {"root": str(runtime_root().resolve()), "active": pointer},
    }
