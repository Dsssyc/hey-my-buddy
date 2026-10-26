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
    process: psutil.Process
    create_time: float
    name: str
    read: str
    error: str

    @property
    def pid(self) -> int:
        return self.process.pid

    def __str__(self) -> str:
        return (f'process path observation denied: pid={self.pid} createTime={self.create_time!r} '
                f'name={self.name!r} read={self.read}: {self.error}')


class _IdentityUnavailable(Exception):
    """A failed identity recheck must never be treated as a denied path read."""


def _read_with_retry(process, read, *, errors=(psutil.Error, OSError)):
    try:
        return read(process)
    except psutil.NoSuchProcess:
        raise
    except errors:
        try:
            current = psutil.Process(process.pid)
            # Construction can retain an unreadable identity on Windows.
            # Require readable evidence, but never compare wall-clock values.
            current.create_time()
        except psutil.NoSuchProcess:
            raise
        except (psutil.Error, OSError) as error:
            raise _IdentityUnavailable(f'{type(error).__name__}: {error}') from error
        if current != process:
            raise psutil.NoSuchProcess(process.pid)
        return read(current)


def _failure(process, created, operation, error, *, name=None) -> str:
    if name is None:
        try:
            name = process.name()
        except (psutil.Error, OSError, SystemError) as name_error:
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
            argv = _read_with_retry(process, lambda current: current.cmdline(),
                                    errors=(psutil.Error, OSError, SystemError))
        except psutil.NoSuchProcess:
            continue
        except _IdentityUnavailable as error:
            problems.append(_failure(process, created, 'identity recheck', error))
        # On macOS psutil 7.2.2 can raise SystemError when proc_cmdline
        # leaves an exception set on its forced-permission-denial path.
        except (psutil.Error, OSError, SystemError) as error:
            if operation == 'create_time' or not _starting_or_ending(process):
                problems.append(_failure(process, created, operation, error))
        else:
            rows.append((process, argv))
    return rows, problems


def _proc_fd_paths(pid: int, paths: set[str]) -> None:
    try:
        handles = list((Path('/proc') / str(pid) / 'fd').iterdir())
    except (FileNotFoundError, ProcessLookupError):
        raise psutil.NoSuchProcess(pid) from None
    for handle in handles:
        try:
            paths.add(os.readlink(handle))
        except FileNotFoundError:
            # A descriptor closed after listing no longer holds a reference.
            continue


def observe_open_paths() -> tuple[dict[int, set[str]], str | None, bool, list[DeniedPathObservation]]:
    """Path references, observation failure, channel availability and denials.

    psutil supplies cwd and Linux mappings. Linux descriptors use /proc;
    macOS descriptors and mappings use lsof, excluding the cwd already observed.
    open_files omits directories, deleted files and some Windows handles. Windows
    uses only cwd for cheap positive evidence and never proves completeness.
    """
    processes, known = _processes()
    problems = [] if known else ['psutil process enumeration incomplete']
    denied: dict[int, list[DeniedPathObservation]] = {}
    paths: dict[int, set[str]] = {}
    for process in processes:
        created = None
        try:
            created = process.create_time()
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError) as error:
            problems.append(_failure(process, created, 'create_time', error))
            continue
        values: set[str] = set()
        readers = [('cwd', lambda current: values.add(current.cwd()))]
        if psutil.LINUX:
            readers.extend([
                ('memory_maps', lambda current: values.update(item.path for item in current.memory_maps())),
                ('/proc/pid/fd', lambda current: _proc_fd_paths(current.pid, values)),
            ])
        process_denied = []
        process_problems = []
        ended = False
        for operation, read in readers:
            try:
                _read_with_retry(process, read)
            except psutil.NoSuchProcess:
                ended = True
                break
            except _IdentityUnavailable as error:
                process_problems.append(_failure(process, created, 'identity recheck', error))
                break
            except (psutil.Error, OSError) as error:
                if _starting_or_ending(process):
                    ended = True
                    break
                if (isinstance(error, psutil.AccessDenied) or
                        operation == '/proc/pid/fd' and isinstance(error, PermissionError)):
                    try:
                        name = process.name()
                    except psutil.NoSuchProcess:
                        ended = True
                        break
                    except (psutil.Error, OSError, SystemError) as name_error:
                        unavailable = f'<unavailable: {type(name_error).__name__}: {name_error}>'
                        process_problems.append(_failure(
                            process, created, 'name after denied ' + operation, name_error, name=unavailable))
                    else:
                        process_denied.append(DeniedPathObservation(
                            process, created, name, operation, f'{type(error).__name__}: {error}'))
                else:
                    process_problems.append('process path observation failed: ' +
                                            _failure(process, created, operation, error))
        if not ended:
            paths[process.pid] = values
            problems.extend(process_problems)
            if process_denied:
                denied[process.pid] = process_denied
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
    return normalized, '; '.join(dict.fromkeys(problems)) or None, available, [
        entry for entries in denied.values() for entry in entries]
