"""psutil boundary fixtures; no machine process or harness is inspected."""
from contextlib import contextmanager
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from hey_my_buddy.blackboard.tasks import storage


def process(pid, argv=(), *, files=(), cwd='/', mappings=(), uid=None, effective_uid=None):
    uid = getattr(os, 'geteuid', lambda: 0)() if uid is None else uid
    effective_uid = uid if effective_uid is None else effective_uid
    return SimpleNamespace(
        pid=pid, create_time=Mock(return_value=123.0),
        status=Mock(return_value=storage.psutil.STATUS_RUNNING),
        cmdline=Mock(return_value=list(argv)), cwd=Mock(return_value=cwd),
        uids=Mock(return_value=SimpleNamespace(real=uid, effective=effective_uid)),
        open_files=Mock(return_value=[SimpleNamespace(path=str(path)) for path in files]),
        memory_maps=Mock(return_value=[SimpleNamespace(path=str(path)) for path in mappings]))


@contextmanager
def processes(*entries, current=None):
    by_pid = {entry.pid: entry for entry in entries}
    opened = set()
    def lookup(pid):
        entry = by_pid[pid] if pid not in opened or current is None else current.get(pid, by_pid[pid])
        opened.add(pid)
        if isinstance(entry, BaseException):
            raise entry
        return entry
    with (patch.object(storage.psutil, 'pids', return_value=list(by_pid)),
          patch.object(storage.psutil, 'Process', side_effect=lookup)):
        yield


@contextmanager
def native_references(output=None, *, stderr='', returncode=0):
    """Only the descriptor/mapping channel psutil cannot replace."""
    def result(argv, **kwargs):
        stdout = output if output is not None else ''.join(
            f'p{pid}\n' for pid in argv[argv.index('-p') + 1].split(','))
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
    with (patch.object(storage.shutil, 'which', return_value='lsof'),
          patch.object(storage.subprocess, 'run', side_effect=result) as command):
        yield command
