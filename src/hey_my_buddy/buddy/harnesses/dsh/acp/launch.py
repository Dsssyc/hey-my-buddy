"""Owned launch of a native ACP process with a forced private DSH home.

Every launch - a real harness, a test fake, or even ``--version``/``--help`` -
must go through :func:`launch`. The caller supplies a private root it created
for this run; the ``DSH_HOME`` directory must already exist inside it as a
real, non-symlink directory, and missing roots, the user's default DSH home,
and any path that resolves through a symlink are rejected before a process
exists. The child environment is the project's ``native_environment`` pass
over the parent environment - its existing allowed keys (identity, locale,
temp, terminal, proxy and CA trust roots) are kept and nothing else crosses,
so no credential variable reaches the child - plus the validated homes set
last: ``DSH_HOME`` is always this run's private directory, and ``HOME`` stays
at its inherited value unless the caller explicitly provides a private home,
which is validated exactly as before (missing, non-directory, symlink,
out-of-root, the user's real home and the user's default DSH home are all
refused). ``HOME`` and ``DSH_HOME`` are reserved: an extra key by either name
is refused, so no argument can bypass the explicit entry or the validation or
silently redirect the child to a default or outside home. Log paths are bound
to the private root through the project's private-path guards and can never
be steered outside it by an argument. The log records argv, home paths and
environment key names; it never records an environment value and this module
never reads a credential file.

A failure after the child exists (launch-bookkeeping or connection-start
errors) never loses ownership: the child is finalized as far as observation
allows and the error that surfaces carries the process, the handle and the
stop evidence, so the caller can adopt or keep waiting on it.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
from pathlib import Path

from ..... import private_dirs
from .....errors import BoardError
from ... import discovery
from ...base import ProcessHandle
from ....runtime.windows_process import owned_popen
from .connection import group_observation

#: Extra environment keys that may never override the validated private homes.
RESERVED_ENV_KEYS = ("HOME", "DSH_HOME")


def real_user_home() -> Path:
    """The operating-system home of the current user."""
    if os.name == "nt":
        return Path.home()
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def default_dsh_home() -> Path:
    """The user's default DSH home; a launch may never write there."""
    return real_user_home() / ".dsh"


class LaunchRejected(BoardError):
    """A pre-spawn validation refused this launch; no process was started."""

    def __init__(self, message: str, **details):
        super().__init__("ACP_LAUNCH_REJECTED", message, **details)


class LaunchOwnershipError(BoardError):
    """A post-spawn step failed; the child's ownership and stop evidence travel
    with this error so the caller can adopt the process or keep waiting."""

    def __init__(self, message: str, *, process, handle, evidence: dict):
        super().__init__("ACP_LAUNCH_OWNERSHIP", message)
        self.process = process
        self.handle = handle
        self.evidence = evidence


def _violations(directory: Path, root: Path, role: str) -> list[str]:
    """Every reason ``directory`` is not an acceptable private ``role`` directory."""
    problems: list[str] = []
    import stat

    try:
        info = os.lstat(directory)
    except OSError:
        return [f"{role} does not exist: {directory}"]
    if stat.S_ISLNK(info.st_mode):
        problems.append(f"{role} is a symlink: {directory}")
    elif not stat.S_ISDIR(info.st_mode):
        problems.append(f"{role} is not a directory: {directory}")
    absolute = Path(os.path.abspath(directory))
    try:
        resolved = Path(os.path.realpath(absolute))
    except (OSError, RuntimeError):
        return problems + [f"{role} cannot be resolved: {directory}"]
    if str(absolute) != str(resolved):
        problems.append(f"{role} resolves through a symlink: {directory}")
    real_root = Path(os.path.realpath(root))
    if resolved != real_root and real_root not in resolved.parents:
        problems.append(f"{role} is outside the private root: {directory}")
    daily = default_dsh_home()
    try:
        daily_resolved = daily.resolve()
    except (OSError, RuntimeError):
        daily_resolved = daily
    if resolved == daily_resolved or daily_resolved in resolved.parents or resolved in daily_resolved.parents:
        problems.append(f"{role} touches the user's default DSH home: {directory}")
    if role == "home" and resolved == real_user_home():
        problems.append(f"{role} is the user's real home directory: {directory}")
    return problems


def ensure_private_log_path(path: Path, root: Path) -> Path:
    """Bind one log path to this run's private root with the project's guards.

    Every check - the root itself, absolute position, default-root and link
    components, containment - runs before anything is created or chmod'd, so a
    rejected path never touches the outside object. Only after validation does
    the parent directory get created (0700), and only inside the validated
    private root.
    """
    path = Path(path)
    root_problems = _violations(Path(root), Path(root), "private root")
    if root_problems:
        raise LaunchRejected("; ".join(root_problems), root=str(root))
    linked = private_dirs.linked_component(path)
    if linked is not None:
        raise LaunchRejected("log path contains a linked component", path=str(path), linked=str(linked))
    resolved_root = Path(os.path.realpath(root))
    parent = Path(os.path.abspath(path.parent))
    try:
        resolved_parent = Path(os.path.realpath(parent))
    except (OSError, RuntimeError):
        raise LaunchRejected("log path cannot be resolved", path=str(path)) from None
    if resolved_parent != resolved_root and resolved_root not in resolved_parent.parents:
        raise LaunchRejected("log path is outside the private root", path=str(path))
    private_dirs.ensure_private_dir(parent)
    return path


def child_environment(dsh_home: Path, home: Path | None = None, extra_env: dict | None = None,
                      *, source: dict | None = None) -> dict:
    """The native environment plus this run's forced homes.

    The parent environment goes through :func:`native_environment`, so exactly
    its existing allowed keys are kept - identity, locale, temp, terminal,
    proxy and CA trust roots - and no other parent or credential variable is
    brought in. ``DSH_HOME`` is always set to this run's validated private
    directory. ``HOME`` keeps its inherited value unless the caller explicitly
    provides the run's private home. Reserved keys are refused in
    ``extra_env``, and the validated homes are set after any merge, so an
    extra key can never override them.
    """
    if extra_env:
        overlap = sorted(set(extra_env) & set(RESERVED_ENV_KEYS))
        if overlap:
            raise LaunchRejected(
                "extra environment may not override the reserved private-home keys",
                keys=overlap)
    environment = discovery.native_environment(dict(os.environ if source is None else source))
    if extra_env:
        environment.update({str(key): str(value) for key, value in extra_env.items()})
    environment["DSH_HOME"] = str(dsh_home)
    if home is not None:
        environment["HOME"] = str(home)
    return environment


def record_launch(log_path: Path, argv: list[str], dsh_home: Path, home: Path | None,
                  environment: dict, *, spawn_cwd: Path | None, rejected: list[str] | None,
                  pid: int | None = None) -> None:
    """Append one launch record through the guarded private-file open; environment
    values are never logged."""
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
        "argv": argv,
        "dshHome": str(dsh_home),
        "home": str(home) if home is not None else None,
        "envKeys": sorted(environment.keys()),
        "spawnCwd": str(spawn_cwd) if spawn_cwd is not None else None,
        "rejected": rejected,
        "pid": pid,
    }
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = private_dirs.open_regular_fd(
        Path(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def finalize_after_spawn_failure(process, handle, *, settle_seconds: float = 5.0) -> dict:
    """Hold and close down a child whose launch bookkeeping failed.

    The child already exists, so ownership is never dropped and this function
    never raises: every step is guarded, each failure lands in the evidence as
    a conservative error, and unknown stays unknown - never stopped. The
    returned evidence states honestly whether the stop is confirmed.
    """
    evidence: dict = {"phase": "post-spawn-launch-failure", "finalizeErrors": []}

    def guarded(step, note):
        try:
            step()
        except Exception as error:  # noqa: BLE001 - finalize must never raise past the child
            evidence["finalizeErrors"].append(f"{note}: {type(error).__name__}: {error}")

    def close_stdin():
        stdin = process.stdin
        if stdin is not None and not stdin.closed:
            stdin.close()

    def await_exit(seconds):
        if process.poll() is None:
            try:
                process.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                evidence["exitWaitTimeout"] = True

    def observe_group():
        evidence["groupObserved"] = group_observation(handle)
        evidence["leaderExitCode"] = process.poll()

    guarded(close_stdin, "stdin close")
    guarded(lambda: await_exit(settle_seconds), "exit wait")
    guarded(observe_group, "first observation")
    if evidence.get("leaderExitCode") is None or evidence.get("groupObserved") != "gone":
        guarded(lambda: handle.terminate(grace_seconds=2.0), "terminate")
        guarded(lambda: await_exit(5), "terminate wait")
        guarded(observe_group, "final observation")
    evidence["shutdownConfirmed"] = bool(evidence.get("leaderExitCode") is not None
                                         and evidence.get("groupObserved") == "gone")
    return evidence


def launch(argv: list[str], *, private_root: Path, dsh_home: Path | None = None,
           home: Path | None = None, extra_env: dict | None = None,
           cwd: Path | None = None, launch_log: Path | None = None) -> tuple[subprocess.Popen, ProcessHandle]:
    """Validate this run's private directories, then spawn one owned process group.

    ``DSH_HOME`` is always forced into the verified private root: ``dsh_home``
    defaults to ``<private root>/dsh-home`` and must exist there as a real,
    non-symlink directory. ``home`` is optional - without it the child keeps
    the parent's inherited ``HOME``; with it, the explicit private home is
    validated exactly like ``DSH_HOME``. Any validation failure raises
    :class:`LaunchRejected` before a process exists and is recorded in the
    launch log. A failure after the child exists raises
    :class:`LaunchOwnershipError` carrying the process, handle and stop
    evidence. The caller keeps both objects: EOF, a session close or a cancel
    acknowledgement never proves the group stopped - only this handle's
    conservative observation does.
    """
    if not argv:
        raise LaunchRejected("launch requires a non-empty argv")
    if private_root is None:
        raise LaunchRejected("launch requires an explicit private root for this run")
    root = Path(private_root)
    root_problems = _violations(root, root, "private root")
    if root_problems:
        # The root itself is untrustworthy; nothing is created or written there.
        raise LaunchRejected("; ".join(root_problems), argv=argv)
    dsh = Path(dsh_home) if dsh_home is not None else root / "dsh-home"
    user_home = Path(home) if home is not None else None
    problems = _violations(dsh, root, "dsh-home")
    if user_home is not None:
        problems = problems + _violations(user_home, root, "home")
    spawn_cwd = Path(cwd) if cwd is not None else root
    log_path = ensure_private_log_path(
        Path(launch_log) if launch_log is not None else root / "logs" / "launches.jsonl", root)
    environment = child_environment(dsh, user_home, extra_env)
    if problems:
        record_launch(log_path, argv, dsh, user_home, environment,
                      spawn_cwd=spawn_cwd, rejected=problems)
        raise LaunchRejected("; ".join(problems), argv=argv)
    try:
        process = owned_popen([str(part) for part in argv], env=environment, cwd=str(spawn_cwd),
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              start_new_session=True, close_fds=True)
    except OSError as error:
        record_launch(log_path, argv, dsh, user_home, environment,
                      spawn_cwd=spawn_cwd, rejected=[f"spawn failed: {error}"])
        raise
    try:
        record_launch(log_path, argv, dsh, user_home, environment,
                      spawn_cwd=spawn_cwd, rejected=None, pid=process.pid)
        handle = ProcessHandle(process, own_group=True, log_paths={})
    except BaseException as error:
        handle = ProcessHandle(process, own_group=True, log_paths={})
        evidence = finalize_after_spawn_failure(process, handle)
        raise LaunchOwnershipError(f"launch bookkeeping failed after spawn: {error}",
                                   process=process, handle=handle, evidence=evidence) from error
    return process, handle
