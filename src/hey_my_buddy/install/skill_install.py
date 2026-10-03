"""``buddy install``: place the shared skill once and bring the service to it (ADR-015).

The canonical copy lives in ``~/.agents/skills/buddy`` (read by Codex); Claude Code
reaches it through ``~/.claude/skills/buddy``, a symbolic link to that directory. A
second Host running the same version finds the marker and only repairs the link.
The new package coordinates the skill swap and idle service cutover under one
upgrade journal; the old installed launcher never chooses the candidate code.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
import sys

from .. import locking
from ..protocol import schemas
from . import skill_package
from ..errors import BoardError
from .runtime import project_root
from ..protocol.transport import get_state_dir

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


def write_runtime_hint(skill: Path, target: Path) -> None:
    """Bootstrap with a stable interpreter; active-runtime remains authority."""
    from .runtime import runtime_python
    temporary = skill / ('.runtime-python-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(str(runtime_python(target)) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, skill / '.runtime-python')
    finally:
        temporary.unlink(missing_ok=True)


def _is_buddy_skill(path: Path) -> bool:
    try:
        head = (path / "SKILL.md").read_text().split("\n---\n", 1)[0]
    except OSError:
        return False
    return any(line.strip() == f"name: {SKILL}" for line in head.splitlines())


def _matches(source: Path, target: Path, marker: dict) -> bool:
    """A version marker alone cannot prove that an installed skill is intact."""
    current = _marker(target) if target.is_dir() and not target.is_symlink() else None
    if not current or any(current.get(key) != marker.get(key) for key in ('version', 'contract', 'sourceCommit')):
        return False
    from .runtime import iter_assets
    names = ['SKILL.md', 'skill.json', 'scripts/buddy', 'scripts/buddy.cmd', 'scripts/buddy.ps1', 'package/LICENSE']
    names.extend(str(path.relative_to(source)) for path in (source / 'references').glob('*.md'))
    names.extend('package/' + relative for relative, _ in iter_assets(source / 'package'))
    try:
        return all(not (target / name).is_symlink() and (target / name).read_bytes() == (source / name).read_bytes() for name in names)
    except OSError:
        return False


def packaged_skill() -> Path | None:
    """The installed-format skill this process runs from, if any."""
    project = project_root()
    candidate = project.parent
    if project.name == "package" and _marker(candidate) and (candidate / "scripts/buddy").is_file():
        return candidate
    return None


def _place(source: Path, target: Path, marker: dict) -> tuple[str, Path | None]:
    current = _marker(target) if target.is_dir() and not target.is_symlink() else None
    if _matches(source, target, marker):
        return "already-current", None
    if target.is_symlink() or (target.exists() and not current and not _is_buddy_skill(target)):
        raise BoardError("SKILL_TARGET_CONFLICT", f"{target} exists and is not a buddy skill; move it away first")
    staged = Path(tempfile.mkdtemp(prefix=".buddy-install-", dir=target.parent))
    previous = target.with_name(f".buddy-install-previous-{uuid.uuid4().hex}") if target.exists() else None
    try:
        shutil.copytree(source, staged / SKILL, symlinks=True)
        if previous is not None:
            target.rename(previous)
        try:
            (staged / SKILL).rename(target)
        except OSError:
            if previous is not None:
                previous.rename(target)
            raise
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    return ("updated" if current else "installed"), previous


def _link_claude(target: Path) -> dict:
    """Point ~/.claude/skills/buddy at the canonical skill with a symbolic link.

    There is no copy fallback: a second copy could silently drift from the canonical
    skill. Anything that prevents the link fails the install before the service is
    upgraded; rerunning after the fix skips the already-placed skill.
    """
    home = claude_skills_home()
    link = home / SKILL
    if link.is_symlink():
        resolved = Path(os.path.realpath(link))
        if resolved == Path(os.path.realpath(target)):
            return {"path": str(link), "status": "already-linked"}
        if resolved.exists() and not _is_buddy_skill(resolved):
            raise BoardError("SKILL_TARGET_CONFLICT", f"{link} links to a different skill; move it away and rerun install")
        link.unlink()
    elif link.exists():
        raise BoardError("SKILL_TARGET_CONFLICT", f"{link} is a separate directory; move it away and rerun install")
    home.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        raise BoardError("CLAUDE_LINK_FAILED",
                         f"Could not link {link} to {target} ({error.strerror or error}). On Windows, enable Developer "
                         "Mode or run with permission to create symbolic links, then rerun the fixed-version package install command.") from None
    return {"path": str(link), "status": "linked"}


def _check_claude_link(target: Path) -> None:
    link = claude_skills_home() / SKILL
    if link.is_symlink():
        if Path(os.path.realpath(link)) != Path(os.path.realpath(target)):
            raise BoardError("SKILL_TARGET_CONFLICT", f"{link} links to another skill; move it away before installing")
    elif link.exists():
        raise BoardError("SKILL_TARGET_CONFLICT", f"{link} is a separate directory; move it away before installing")


def _legacy_plugins() -> list[str]:
    cache = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "plugins/cache"
    return sorted(str(path) for path in cache.glob("*/hey-my-buddy") if path.is_dir()) if cache.is_dir() else []


def install(params: dict) -> dict:
    schemas.reject_unknown(params, set(), "install")
    if os.environ.get("BUDDY_AGENT_CREDENTIAL") or os.environ.get("BUDDY_AGENT_CREDENTIAL_FILE"):
        raise BoardError("UNAUTHORIZED", "A Worker cannot install the shared skill")
    home = agent_skills_home()
    state = get_state_dir()
    from .runtime import runtime_root
    planned_paths = [str(home / SKILL), str(claude_skills_home() / SKILL), str(state), str(runtime_root())]
    from ..protocol.contracts import CONTRACT_VERSION
    print(json.dumps({'contractVersion': CONTRACT_VERSION, 'action': 'install-plan', 'writePaths': planned_paths}), file=sys.stderr, flush=True)
    if (state / 'board.sqlite3').exists() and not (state / 'upgrade.json').exists():
        from .upgrade import idle_snapshot, layout_readiness
        idle_snapshot(state)
        layout_readiness(state)
    home.mkdir(parents=True, exist_ok=True)
    target = home / SKILL
    lock = os.open(home / f".{SKILL}-install.lock", os.O_CREAT | os.O_RDWR, 0o600)
    built = None
    try:
        locking.lock(lock)
        if (state / 'upgrade.json').exists():
            from .upgrade import upgrade
            upgrade({})  # Recover the recorded generation before starting another install.
        if (state / 'board.sqlite3').exists():
            from .upgrade import idle_snapshot, layout_readiness
            idle_snapshot(state)
            layout_readiness(state)
        _check_claude_link(target)
        source = packaged_skill()
        if source is None:
            built = Path(tempfile.mkdtemp(prefix=".buddy-source-"))
            skill_package.assemble(project_root(), built / SKILL)
            source = built / SKILL
        marker = _marker(source)
        state = get_state_dir()
        pointer = state / "active-runtime.json"
        from . import runtime
        from .launcher import selected_runtime
        intact = _matches(source, target, marker)
        if source.resolve() == target.resolve() and not intact:
            raise BoardError('SKILL_INVALID', 'The installed copy is incomplete; rerun the fixed-version package install command')
        if pointer.exists() and intact:
            active = selected_runtime(state, allow_overrides=False)
            expected = runtime.runtime_dir(source / 'package')
            if active == expected and runtime.content_id(active) == active.name:
                claude = _link_claude(target)
                write_runtime_hint(target, active)
                return {'skill': {'path': str(target), 'version': marker['version'], 'contract': marker['contract'], 'placement': 'already-current'},
                        'claude': claude, 'service': {'action': 'none', 'reason': 'same complete generation'}, 'writePaths': planned_paths,
                        'diagnostics': {'skillInstalled': True, 'claudeLink': claude['status'], 'runtimeMaterialized': True,
                                        'launcher': str(target / 'scripts/buddy')}}
        if pointer.exists() and not (state / "control.json").exists():
            if (state / "board.sqlite3").exists():
                from .upgrade import idle_snapshot
                idle_snapshot(state)
            from .launcher import selected_runtime
            from ..protocol.transport import ensure_service
            old_dev = os.environ.pop("BUDDY_DEV_SOURCE", None)
            old_pin = os.environ.get("BUDDY_RUNTIME")
            try:
                os.environ.pop("BUDDY_RUNTIME", None)
                previous = selected_runtime(state, allow_overrides=False)
                os.environ["BUDDY_RUNTIME"] = str(previous)
                ensure_service(state)
            finally:
                if old_dev is not None:
                    os.environ["BUDDY_DEV_SOURCE"] = old_dev
                if old_pin is None:
                    os.environ.pop("BUDDY_RUNTIME", None)
                else:
                    os.environ["BUDDY_RUNTIME"] = old_pin
        if (state / "control.json").exists():
            from .upgrade import upgrade
            current = _marker(target) if target.is_dir() and not target.is_symlink() else None
            # Placement follows the actual content comparison above, not a marker
            # match: a same-version rebuild with changed files is an update.
            placement = "already-current" if intact else ("updated" if current else "installed")
            result = upgrade({}, skill_source=source, skill_target=target)
            if result.get("error"):
                error = result["error"]
                raise BoardError(error["code"], error["message"], **{**error.get('details', {}), **{k: v for k, v in result.items() if k != "error"}})
            claude = {"path": str(claude_skills_home() / SKILL), "status": _claude_status(target)}
            service = {"action": "upgrade", "result": result}
        else:
            from . import runtime
            from .launcher import write_active_runtime
            if (state / "board.sqlite3").exists():
                raise BoardError("UPGRADE_NO_ROLLBACK_RUNTIME",
                                 "Existing board has no provable service runtime; recover its previous package before installing")
            installed = runtime.materialize(root=source / 'package')
            ready = runtime.read_ready(Path(installed["runtimeDir"]))
            if ready.get("sourceCommit") != marker.get("sourceCommit"):
                raise BoardError("INSTALL_GENERATION_MISMATCH", "Skill and materialized runtime came from different source generations")
            placement, previous_skill = (("already-current", None) if Path(os.path.realpath(source)) == Path(os.path.realpath(target)) else _place(source, target, marker))
            created_link = False
            try:
                claude = _link_claude(target)
                created_link = claude["status"] == "linked"
                state.mkdir(mode=0o700, parents=True, exist_ok=True)
                write_runtime_hint(target, Path(installed['runtimeDir']))
                write_active_runtime(state, Path(installed["runtimeDir"]))
            except Exception:
                pointer.unlink(missing_ok=True)
                if created_link:
                    (claude_skills_home() / SKILL).unlink(missing_ok=True)
                if previous_skill is not None:
                    shutil.rmtree(target)
                    previous_skill.rename(target)
                elif placement == "installed":
                    shutil.rmtree(target)
                raise
            if previous_skill is not None:
                shutil.rmtree(previous_skill, ignore_errors=True)
            service = {"action": "none", "reason": "the first command starts the installed runtime"}
    finally:
        if built is not None:
            shutil.rmtree(built, ignore_errors=True)
        locking.unlock(lock)
        os.close(lock)
    result = {"skill": {"path": str(target), "version": marker["version"], "contract": marker["contract"],
                        "placement": placement},
              "claude": claude, "service": service}
    result['writePaths'] = planned_paths
    result['diagnostics'] = {'skillInstalled': True, 'claudeLink': claude['status'], 'runtimeMaterialized': True,
                             'launcher': str(target / 'scripts/buddy'),
                             'next': 'A new Host session loads the skill. This session can use the absolute launcher path outside its sandbox.'}
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
