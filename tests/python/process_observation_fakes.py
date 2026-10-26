"""psutil boundary fixtures; no machine process or harness is inspected."""
from contextlib import contextmanager
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from hey_my_buddy import process_observation as observation


def process(pid, argv=(), *, files=(), cwd='/', mappings=(), uid=None, effective_uid=None,
            username='fixture-account', created=1234.5, name='fixture-process'):
    uid = getattr(os, 'geteuid', lambda: 0)() if uid is None else uid
    effective_uid = uid if effective_uid is None else effective_uid
    return SimpleNamespace(
        pid=pid,
        create_time=Mock(return_value=created), name=Mock(return_value=name),
        status=Mock(return_value=observation.psutil.STATUS_RUNNING),
        cmdline=Mock(return_value=list(argv)), cwd=Mock(return_value=cwd),
        uids=Mock(return_value=SimpleNamespace(real=uid, effective=effective_uid)),
        username=Mock(return_value=username),
        open_files=Mock(return_value=[SimpleNamespace(path=str(path)) for path in files]),
        memory_maps=Mock(return_value=[SimpleNamespace(path=str(path)) for path in mappings]))


@contextmanager
def processes(*entries, current_username='fixture-account'):
    by_pid = {entry.pid: entry for entry in entries}
    current = process(0, username=current_username)
    def lookup(pid=None):
        return current if pid is None else by_pid[pid]
    with (patch.object(observation.psutil, 'pids', return_value=list(by_pid)),
          patch.object(observation.psutil, 'Process', side_effect=lookup)):
        yield current


@contextmanager
def native_references(output=None, *, stderr='', returncode=0):
    """Only the descriptor/mapping channel psutil cannot replace."""
    def result(argv, **kwargs):
        stdout = output if output is not None else ''.join(
            f'p{pid}\n' for pid in argv[argv.index('-p') + 1].split(','))
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
    with (patch.object(observation.shutil, 'which', return_value='lsof'),
          patch.object(observation.subprocess, 'run', side_effect=result) as command):
        yield command


@contextmanager
def proc_references(*paths):
    """One Linux descriptor list; entries may raise a readlink error."""
    handles = [Path('/proc/fixture/fd') / str(index) for index in range(len(paths))]
    values = dict(zip(handles, paths))
    def read(handle):
        value = values[handle]
        if isinstance(value, Exception):
            raise value
        return value
    with (patch.object(Path, 'iterdir', return_value=handles),
          patch.object(observation.os, 'readlink', side_effect=read)):
        yield
