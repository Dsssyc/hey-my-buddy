"""``hey-my-buddy install``: place the shared skill once and bring the service to it (ADR-015).

The canonical copy lives in ``~/.agents/skills/hey-my-buddy`` (read by Codex); Claude Code
reaches it through ``~/.claude/skills/hey-my-buddy``, a directory link to that directory. A
second Host running the same version finds the marker and only repairs the link.
The new package coordinates the skill swap and service cutover under one
upgrade journal; the old installed launcher never chooses the candidate code.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import uuid
import sys

from .. import private_dirs
from ..protocol import schemas
from . import skill_package
from ..errors import BoardError
from .runtime import project_root
from ..state_root import check_state_root, get_state_dir

SKILL = skill_package.SKILL_NAME


def agent_skills_home() -> Path:
    return Path(os.environ.get("HMB_AGENT_SKILLS_DIR") or Path.home() / ".agents/skills").expanduser().absolute()


def claude_skills_home() -> Path:
    return Path(os.environ.get("HMB_CLAUDE_SKILLS_DIR") or Path.home() / ".claude/skills").expanduser().absolute()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _create_claude_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        from _winapi import CreateJunction
        CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _remove_claude_link(link: Path) -> None:
    """Remove only the link, including a junction whose target is missing."""
    if link.is_junction():
        link.rmdir()
    elif link.is_symlink():
        link.unlink()
    elif link.exists():
        raise BoardError("SKILL_TARGET_CONFLICT", f"{link} is no longer a link; move it away before retrying")


def _marker(skill: Path) -> dict | None:
    try:
        value = json.loads((skill / "skill.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("name") == SKILL else None


def write_runtime_hint(skill: Path, target: Path) -> None:
    """Bootstrap with a stable interpreter; active-runtime remains authority."""
    from .runtime import runtime_python
    from ..private_files import publish_bytes
    publish_bytes(skill / '.runtime-python', (str(runtime_python(target)) + '\n').encode("utf-8"))


def _is_product_skill(path: Path) -> bool:
    try:
        head = (path / "SKILL.md").read_text(encoding="utf-8").split("\n---\n", 1)[0]
    except OSError:
        return False
    return any(line.strip() == f"name: {SKILL}" for line in head.splitlines())


def _matches(source: Path, target: Path, marker: dict) -> bool:
    """A version marker alone cannot prove that an installed skill is intact."""
    current = _marker(target) if target.is_dir() and not _is_link(target) else None
    if not current or any(current.get(key) != marker.get(key) for key in ('version', 'contract', 'sourceCommit')):
        return False
    from .runtime import iter_assets
    names = ['SKILL.md', 'skill.json', 'scripts/hey-my-buddy', 'scripts/hey-my-buddy.cmd', 'scripts/hey-my-buddy.ps1', 'package/LICENSE']
    names.extend(str(path.relative_to(source)) for path in (source / 'references').glob('*.md'))
    names.extend('package/' + relative for relative, _ in iter_assets(source / 'package'))
    try:
        return all(not _is_link(target / name) and (target / name).read_bytes() == (source / name).read_bytes() for name in names)
    except OSError:
        return False


def packaged_skill() -> Path | None:
    """The installed-format skill this process runs from, if any."""
    project = project_root()
    candidate = project.parent
    if project.name == "package" and _marker(candidate) and (candidate / "scripts/hey-my-buddy").is_file():
        return candidate
    return None


def _place(source: Path, target: Path, marker: dict) -> tuple[str, Path | None]:
    current = _marker(target) if target.is_dir() and not _is_link(target) else None
    if _matches(source, target, marker):
        return "already-current", None
    if _is_link(target) or (target.exists() and not current and not _is_product_skill(target)):
        raise BoardError("SKILL_TARGET_CONFLICT", f"{target} exists and is not a hey-my-buddy skill; move it away first")
    staged = private_dirs.make_temporary_dir(prefix=".hey-my-buddy-install-", directory=target.parent)
    previous = target.with_name(f".hey-my-buddy-install-previous-{uuid.uuid4().hex}") if target.exists() else None
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
    """Use an NTFS junction on Windows and a symbolic link on POSIX.

    There is no copy fallback: a second copy could silently drift from the canonical
    skill. Anything that prevents the link fails the install before the service is
    upgraded; rerunning after the fix skips the already-placed skill.
    """
    home = claude_skills_home()
    link = home / SKILL
    if _check_claude_link(target):
        return {"path": str(link), "status": "already-linked"}
    try:
        home.mkdir(parents=True, exist_ok=True)
        _create_claude_link(link, target)
        if _claude_status(target) != "linked":
            _remove_claude_link(link)
            raise OSError("the created directory link does not resolve to the canonical skill")
    except OSError as error:
        raise BoardError("CLAUDE_LINK_FAILED",
                         f"Could not link {link} to {target} ({error.strerror or error}). "
                         + ("Use writable directories on a local NTFS volume and check the target; " if os.name == "nt" else
                            "Check the target and permissions on the link directory; ")
                         + "then rerun the fixed-version package install command.") from None
    return {"path": str(link), "status": "linked"}


def _check_claude_link(target: Path) -> bool:
    link = claude_skills_home() / SKILL
    if _is_link(link):
        if Path(os.path.realpath(link)) != Path(os.path.realpath(target)):
            raise BoardError("SKILL_TARGET_CONFLICT", f"{link} links to another skill; move it away before installing")
        return True
    elif link.exists():
        raise BoardError("SKILL_TARGET_CONFLICT", f"{link} is a separate directory; move it away before installing")
    return False


def install(params: dict) -> dict:
    schemas.reject_unknown(params, set(), "install")
    if os.environ.get("HMB_AGENT_CREDENTIAL_FILE"):
        raise BoardError("UNAUTHORIZED", "A Worker cannot install the shared skill")
    home = agent_skills_home()
    state = check_state_root(get_state_dir(), create=True)
    from .runtime import runtime_root
    planned_paths = [str(home / SKILL), str(claude_skills_home() / SKILL), str(state), str(runtime_root())]
    from ..protocol.contracts import CONTRACT_VERSION
    print(json.dumps({'contractVersion': CONTRACT_VERSION, 'action': 'install-plan', 'writePaths': planned_paths}), file=sys.stderr, flush=True)
    home.mkdir(parents=True, exist_ok=True)
    target = home / SKILL
    lock = private_dirs.open_lock_fd(home / f".{SKILL}-install.lock")
    built = None
    recovery_result = None
    skill_cleanup_error = None
    try:
        if (state / 'upgrade.json').exists():
            from .upgrade import upgrade
            recovery_result = upgrade(state)
        _check_claude_link(target)
        source = packaged_skill()
        if source is None:
            built = private_dirs.make_temporary_dir(prefix=".hey-my-buddy-source-",
                directory=private_dirs.ensure_private_dir(state / "tmp"))
            skill_package.assemble(project_root(), built / SKILL)
            source = built / SKILL
        marker = _marker(source)
        pointer = state / "active-runtime.json"
        from . import runtime
        from .launcher import selected_runtime
        intact = _matches(source, target, marker)
        if source.resolve() == target.resolve() and not intact:
            raise BoardError('SKILL_INVALID', 'The installed copy is incomplete; rerun the fixed-version package install command')
        if pointer.exists() and intact:
            active = selected_runtime(state, allow_overrides=False)
            expected = runtime.runtime_dir(source / 'package')
            if active == expected:
                claude = _link_claude(target)
                write_runtime_hint(target, active)
                return {'skill': {'path': str(target), 'version': marker['version'], 'contract': marker['contract'], 'placement': 'already-current'},
                        'claude': claude, 'service': {'action': 'none', 'reason': 'same complete generation'}, 'writePaths': planned_paths,
                        **({'recovery': recovery_result} if recovery_result is not None else {}),
                        'diagnostics': {'skillInstalled': True, 'claudeLink': claude['status'], 'runtimeMaterialized': True,
                                        'launcher': str(target / 'scripts/hey-my-buddy')}}
        if pointer.exists() and not (state / "control.json").exists():
            from .launcher import selected_runtime
            from .upgrade import start
            old_dev = os.environ.pop("HMB_DEV_SOURCE", None)
            try:
                previous = selected_runtime(state, allow_overrides=False)
                start(state, previous)
            finally:
                if old_dev is not None:
                    os.environ["HMB_DEV_SOURCE"] = old_dev
        if (state / "control.json").exists():
            from .upgrade import upgrade
            current = _marker(target) if target.is_dir() and not _is_link(target) else None
            # Placement follows the actual content comparison above, not a marker
            # match: a same-version rebuild with changed files is an update.
            placement = "already-current" if intact else ("updated" if current else "installed")
            result = upgrade(state, skill_source=source, skill_target=target)
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
                from ..private_dirs import ensure_private_dir
                ensure_private_dir(state)
                write_runtime_hint(target, Path(installed['runtimeDir']))
                write_active_runtime(state, Path(installed["runtimeDir"]))
            except Exception:
                pointer.unlink(missing_ok=True)
                if created_link:
                    _remove_claude_link(claude_skills_home() / SKILL)
                if previous_skill is not None:
                    shutil.rmtree(target)
                    previous_skill.rename(target)
                elif placement == "installed":
                    shutil.rmtree(target)
                raise
            if previous_skill is not None:
                try:
                    shutil.rmtree(previous_skill)
                except OSError as error:
                    skill_cleanup_error = f'{previous_skill}: {error}'
            service = {"action": "none", "reason": "the first command starts the installed runtime"}
    except BoardError as error:
        if recovery_result is not None:
            error.details['recovery'] = recovery_result
        raise
    finally:
        if built is not None:
            shutil.rmtree(built, ignore_errors=True)
        os.close(lock)
    result = {"skill": {"path": str(target), "version": marker["version"], "contract": marker["contract"],
                        "placement": placement},
              "claude": claude, "service": service,
              **({'recovery': recovery_result} if recovery_result is not None else {}),
              **({'skillCleanupError': skill_cleanup_error} if skill_cleanup_error is not None else {})}
    result['writePaths'] = planned_paths
    result['diagnostics'] = {'skillInstalled': True, 'claudeLink': claude['status'], 'runtimeMaterialized': True,
                             'launcher': str(target / 'scripts/hey-my-buddy'),
                             'next': 'A new Host session loads the skill. This session can use the absolute launcher path outside its sandbox.'}
    return result


def _claude_status(target: Path) -> str:
    link = claude_skills_home() / SKILL
    if _is_link(link):
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
    from .launcher import read_private
    pointer = read_private(state / "active-runtime.json")
    return {
        "skill": {"dir": str(target), "installed": marker is not None,
                  "version": marker.get("version") if marker else None,
                  "launcher": str(target / "scripts" / SKILL)},
        "claude": {"path": str(claude_skills_home() / SKILL), "status": _claude_status(target)},
        "invokedFrom": str(running) if running else str(project_root()),
        "data": {"stateDir": str(state), "board": str(state / "board.sqlite3"),
                 "attempts": str(state / "attempts"), "workspaces": str(state / "workspaces"),
                 "backup": str(state / "backups" / "current"), "serviceLog": str(state / "control.log")},
        "runtime": {"root": str(runtime_root().resolve()), "active": pointer['runtimeDir'] if pointer is not None else None},
    }
