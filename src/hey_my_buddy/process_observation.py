"""Read-only process observations shared by storage and private-root checks."""
from __future__ import annotations

from dataclasses import dataclass
import locale
import os
from pathlib import Path
import shutil
import subprocess

import psutil


@dataclass(frozen=True)
class DeniedPathObservation:
    pid: int
    create_time: float
    name: str
    read: str
    error: str

    def __str__(self) -> str:
        return (f'process path observation denied: pid={self.pid} createTime={self.create_time!r} '
                f'name={self.name!r} read={self.read}: {self.error}')


class _IdentityUnavailable(Exception):
    """A failed identity recheck must never be treated as a denied path read."""


def _read_with_retry(process, created, read, *, errors=(psutil.Error, OSError)):
    try:
        return read(process)
    except psutil.NoSuchProcess:
        raise
    except errors:
        try:
            current = psutil.Process(process.pid)
            current_created = current.create_time()
        except psutil.NoSuchProcess:
            raise
        except (psutil.Error, OSError) as error:
            raise _IdentityUnavailable(f'{type(error).__name__}: {error}') from error
        if current_created != created:
            raise psutil.NoSuchProcess(process.pid)
        return read(current)


def _failure(process, created, operation, error, *, name=None) -> str:
    if name is None:
        try:
            name = process.name()
        except (psutil.Error, OSError) as name_error:
            name = f'<unavailable: {type(name_error).__name__}: {name_error}>'
    return (f'pid={process.pid} createTime={created!r} name={name!r} '
            f'read={operation}: {type(error).__name__}: {error}')


def _processes() -> tuple[list[psutil.Process], bool]:
    """One PID snapshot, scoped to this account's processes.

    POSIX uses the effective UID; Windows uses the process username. Private
    state belongs to this account; unreadable ownership cannot exclude a PID.
    """
    try:
        pids = psutil.pids()
        owner = psutil.Process().username() if psutil.WINDOWS else os.geteuid()
    except (psutil.Error, OSError):
        return [], False
    processes, known = [], True
    for pid in pids:
        try:
            process = psutil.Process(pid)
            user = process.username() if psutil.WINDOWS else process.uids().effective
            if user == owner:
                processes.append(process)
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError):
            known = False
    return processes, known


def _starting_or_ending(process: psutil.Process) -> bool:
    """After a failed read, macOS idle can mean creation or exit teardown."""
    try:
        return process.status() in (psutil.STATUS_IDLE, psutil.STATUS_ZOMBIE)
    except psutil.NoSuchProcess:
        return True
    except (psutil.Error, OSError):
        return False


def observe_process_argv() -> tuple[list[tuple[psutil.Process, list[str]]], list[str]]:
    """Positive argv observations with native argument boundaries preserved."""
    processes, known = _processes()
    problems = [] if known else ['psutil process enumeration incomplete']
    rows = []
    for process in processes:
        created = None
        operation = 'create_time'
        try:
            created = process.create_time()
            operation = 'cmdline'
            argv = _read_with_retry(process, created, lambda current: current.cmdline(),
                                    errors=(psutil.Error, OSError, SystemError))
        except psutil.NoSuchProcess:
            continue
        except _IdentityUnavailable as error:
            problems.append(_failure(process, created, 'create_time recheck', error))
        # On macOS psutil 7.2.2 can raise SystemError when proc_cmdline
        # leaves an exception set on its forced-permission-denial path.
        except (psutil.Error, OSError, SystemError) as error:
            if operation == 'create_time' or not _starting_or_ending(process):
                problems.append(_failure(process, created, operation, error))
        else:
            rows.append((process, argv))
    return rows, problems


def _proc_fd_paths(pid: int) -> set[str]:
    try:
        handles = list((Path('/proc') / str(pid) / 'fd').iterdir())
    except (FileNotFoundError, ProcessLookupError):
        raise psutil.NoSuchProcess(pid) from None
    paths = set()
    for handle in handles:
        try:
            paths.add(os.readlink(handle))
        except FileNotFoundError:
            # A descriptor closed after listing no longer holds a reference.
            continue
    return paths


def observe_open_paths() -> tuple[dict[int, set[str]], str | None, bool, list[DeniedPathObservation]]:
    """Path references, observation failure, channel availability and denials.

    psutil supplies cwd and Linux mappings. Linux descriptors use /proc;
    macOS descriptors and mappings use lsof, excluding the cwd already observed.
    open_files omits directories, deleted files and some Windows handles. Windows
    uses only cwd for cheap positive evidence and never proves completeness.
    """
    processes, known = _processes()
    problems = [] if known else ['psutil process enumeration incomplete']
    denied: dict[int, DeniedPathObservation] = {}
    paths: dict[int, set[str]] = {}
    for process in processes:
        created = None
        operation = 'create_time'
        try:
            created = process.create_time()
            values = paths.setdefault(process.pid, set())
            operation = 'cwd'
            values.add(_read_with_retry(process, created, lambda current: current.cwd()))
            if psutil.LINUX:
                operation = 'memory_maps'
                values.update(item.path for item in _read_with_retry(
                    process, created, lambda current: current.memory_maps()))
                operation = '/proc/pid/fd'
                values.update(_read_with_retry(
                    process, created, lambda current: _proc_fd_paths(current.pid)))
        except psutil.NoSuchProcess:
            paths.pop(process.pid, None)
        except _IdentityUnavailable as error:
            problems.append(_failure(process, created, 'create_time recheck', error))
        except (psutil.Error, OSError) as error:
            if operation != 'create_time' and _starting_or_ending(process):
                paths.pop(process.pid, None)
            elif operation != 'create_time' and isinstance(error, psutil.AccessDenied):
                try:
                    name = process.name()
                except psutil.NoSuchProcess:
                    paths.pop(process.pid, None)
                except (psutil.Error, OSError) as name_error:
                    problems.append(_failure(process, created, 'name after denied ' + operation, name_error))
                else:
                    denied[process.pid] = DeniedPathObservation(
                        process.pid, created, name, operation, f'{type(error).__name__}: {error}')
                    # macOS lsof must not turn this explicit exception back into
                    # an unknown missing-PID result. Keep any positive paths.
            else:
                problems.append('process path observation failed: ' +
                                _failure(process, created, operation, error))
    available = psutil.LINUX or psutil.MACOS or psutil.WINDOWS
    native_pids = None
    if psutil.WINDOWS:
        problems.append('Windows open-file observation is incomplete: only cwd is observed')
    elif psutil.MACOS and paths:
        binary = shutil.which('lsof')
        if binary is None:
            available = False
        else:
            try:
                result = subprocess.run(
                    [binary, '-nP', '-a', '-p', ','.join(map(str, paths)), '-d', '^cwd', '-Fpn'],
                    capture_output=True, text=True, encoding=locale.getencoding(), timeout=120,
                    check=False)
            except OSError as error:
                problems.append(f'lsof failed: {type(error).__name__}')
            else:
                if result.returncode not in (0, 1) or result.stderr.strip():
                    problems.append(f'lsof observation incomplete (exit {result.returncode})')
                native_pids = set()
                current_pid = None
                for line in result.stdout.splitlines():
                    if line.startswith('p'):
                        current_pid = int(line[1:])
                        native_pids.add(current_pid)
                    elif line.startswith('n'):
                        paths[current_pid].add(line[1:])
    for process in processes:
        if native_pids is None or process.pid not in paths or process.pid in native_pids:
            continue
        if process.pid in denied:
            continue
        if _starting_or_ending(process):
            paths.pop(process.pid)
        else:
            problems.append('lsof observation incomplete for a snapshot process')
    normalized = {pid: {path.removesuffix(' (deleted)') for path in values}
                  for pid, values in paths.items()}
    return normalized, '; '.join(dict.fromkeys(problems)) or None, available, list(denied.values())
