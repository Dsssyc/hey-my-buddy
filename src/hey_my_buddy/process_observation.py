"""Read-only process observations shared by storage and private-root checks."""
from __future__ import annotations

import locale
import os
from pathlib import Path
import shutil
import subprocess

import psutil


def _processes() -> tuple[list[psutil.Process], bool]:
    """One PID snapshot, scoped on POSIX to this effective user's processes.

    Private state belongs to this user; other effective users are outside the
    scope. Creating (idle) processes have not run yet; zombies have ended.
    An unreadable UID or status cannot establish either exclusion.
    """
    try:
        pids = psutil.pids()
    except (psutil.Error, OSError):
        return [], False
    processes, known = [], True
    effective_uid = None if psutil.WINDOWS else os.geteuid()
    for pid in pids:
        try:
            process = psutil.Process(pid)
            if psutil.WINDOWS or process.uids().effective == effective_uid:
                if process.status() not in (psutil.STATUS_IDLE, psutil.STATUS_ZOMBIE):
                    processes.append(process)
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError):
            known = False
    return processes, known


def observe_process_argv() -> tuple[list[tuple[psutil.Process, list[str]]], bool]:
    """Positive argv observations with native argument boundaries preserved."""
    processes, known = _processes()
    rows = []
    for process in processes:
        try:
            rows.append((process, process.cmdline()))
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError):
            known = False
    return rows, known


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


def observe_open_paths() -> tuple[dict[int, set[str]], str | None, bool]:
    """Path references, observation failure, and channel availability.

    psutil supplies cwd and Linux/Windows mappings. Linux descriptors use /proc;
    macOS descriptors and mappings use lsof, excluding the cwd already observed.
    open_files alone omits directories and deleted files; on Windows it can also
    silently omit handles, so Windows observations never prove completeness.
    """
    processes, known = _processes()
    problems = [] if known else ['psutil process enumeration incomplete']
    paths: dict[int, set[str]] = {}
    for process in processes:
        try:
            values = paths.setdefault(process.pid, set())
            values.add(process.cwd())
            if psutil.LINUX or psutil.WINDOWS:
                values.update(item.path for item in process.memory_maps())
            if psutil.LINUX:
                values.update(_proc_fd_paths(process.pid))
            elif psutil.WINDOWS:
                values.update(item.path for item in process.open_files())
        except psutil.NoSuchProcess:
            paths.pop(process.pid, None)
        except (psutil.Error, OSError) as error:
            problems.append(f'process path observation failed: {type(error).__name__}')
    available = psutil.LINUX or psutil.MACOS or psutil.WINDOWS
    native_pids = None
    if psutil.WINDOWS:
        problems.append('Windows open-file observation is incomplete: psutil can omit handles')
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
        try:
            if process.is_running():
                problems.append('lsof has no result for a live snapshot process')
            else:
                paths.pop(process.pid)
        except psutil.NoSuchProcess:
            paths.pop(process.pid)
        except (psutil.Error, OSError) as error:
            problems.append(f'process liveness observation failed: {type(error).__name__}')
    normalized = {pid: {path.removesuffix(' (deleted)') for path in values}
                  for pid, values in paths.items()}
    return normalized, '; '.join(dict.fromkeys(problems)) or None, available
