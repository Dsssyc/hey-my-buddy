"""Interruption and byte-contract checks for private fact publication."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy import private_files


OLD = b'{"generation":"old","complete":true}\n'
NEW = (b'{"generation":"new","payload":"' + b"0123456789abcdef" * 4096
       + b'","complete":true}\n')

# This runs in a separate writer process. The parent stops it while a real file
# operation is in progress, rather than raising an exception in the same process.
WRITER = r'''
import os, stat, sys
from pathlib import Path
from hey_my_buddy import private_files

path, stage, mode, content_path = sys.argv[1:]
content = Path(content_path).read_bytes()
def hold(name):
    if stage == name:
        sys.stdout.write("READY\n")
        sys.stdout.flush()
        sys.stdin.buffer.read(1)

real_open, real_fdopen = os.open, os.fdopen
real_fsync, real_replace = os.fsync, os.replace
real_link, real_unlink = os.link, os.unlink

def intercept_open(path, flags, *args, **kwargs):
    if flags & os.O_EXCL and flags & os.O_WRONLY:
        hold("before_create")
        fd = real_open(path, flags, *args, **kwargs)
        hold("after_create")
        return fd
    return real_open(path, flags, *args, **kwargs)

class Stream:
    def __init__(self, raw): self.raw = raw
    def __enter__(self):
        self.raw.__enter__()
        return self
    def __exit__(self, *args): return self.raw.__exit__(*args)
    def fileno(self): return self.raw.fileno()
    def write(self, data):
        hold("before_write")
        middle = len(data) // 2
        self.raw.write(data[:middle])
        hold("partial_write")
        self.raw.write(data[middle:])
        hold("after_write")
        return len(data)
    def flush(self):
        hold("before_flush")
        result = self.raw.flush()
        hold("after_flush")
        return result

def intercept_fdopen(*args, **kwargs):
    raw = real_fdopen(*args, **kwargs)
    return Stream(raw) if (args[1] if len(args) > 1 else kwargs.get("mode")) == "wb" else raw

def intercept_fsync(fd):
    kind = "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "writer"
    hold("before_" + kind + "_fsync")
    result = real_fsync(fd)
    hold("after_" + kind + "_fsync")
    return result

def intercept_replace(*args, **kwargs):
    hold("before_replace")
    result = real_replace(*args, **kwargs)
    hold("after_replace")
    return result

def intercept_link(*args, **kwargs):
    hold("before_link")
    result = real_link(*args, **kwargs)
    hold("after_link")
    return result

def intercept_unlink(path, *args, **kwargs):
    if str(path).endswith(".tmp"): hold("before_cleanup")
    try:
        return real_unlink(path, *args, **kwargs)
    finally:
        if str(path).endswith(".tmp"): hold("after_cleanup")

os.open, os.fdopen = intercept_open, intercept_fdopen
os.fsync, os.replace = intercept_fsync, intercept_replace
os.link, os.unlink = intercept_link, intercept_unlink
try:
    private_files.publish_bytes(Path(path), content, immutable=(mode == "immutable"))
except FileExistsError:
    sys.stdout.write("CONFLICT\n")
else:
    hold("after_success")
    sys.stdout.write("SUCCESS\n")
sys.stdout.flush()
'''


def _read_marker(process: subprocess.Popen) -> bytes:
    messages: queue.Queue[bytes] = queue.Queue()
    thread = threading.Thread(target=lambda: messages.put(process.stdout.readline()), daemon=True)
    thread.start()
    try:
        return messages.get(timeout=10)
    except queue.Empty:
        process.kill()
        process.communicate(timeout=5)
        raise AssertionError("writer did not reach its publication window")


def _spawn(path: Path, content_path: Path, stage: str, mode: str) -> subprocess.Popen:
    environment = {key: value for key, value in os.environ.items() if key not in {
        "HMB_STATE_DIR", "HMB_RUNTIME_ROOT", "HMB_RUNTIME", "HMB_RUNTIME_IDENTITY",
        "HMB_AGENT_CREDENTIAL_FILE", "HMB_AGENT_CREDENTIAL_FD", "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT"}}
    return subprocess.Popen([sys.executable, "-c", WRITER, str(path), stage, mode,
                             str(content_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=environment)


class PrivatePublicationTests(unittest.TestCase):
    def test_killed_writer_exposes_only_complete_mutable_facts(self):
        stages = ("before_create", "after_create", "before_write", "partial_write",
                  "after_write", "before_flush", "after_flush", "before_writer_fsync",
                  "after_writer_fsync", "before_replace", "after_replace",
                  "before_directory_fsync", "after_directory_fsync", "before_cleanup",
                  "after_cleanup", "after_success")
        if os.name == "nt":
            stages = tuple(stage for stage in stages if "directory_fsync" not in stage)
        for old_exists in (False, True):
            for stage in stages:
                with self.subTest(old_exists=old_exists, stage=stage), tempfile.TemporaryDirectory() as root:
                    path, content_path = Path(root) / "fact.json", Path(root) / "content.bin"
                    content_path.write_bytes(NEW)
                    if old_exists:
                        path.write_bytes(OLD)
                    expected = NEW if stage in {"after_replace", "before_directory_fsync",
                                                "after_directory_fsync", "before_cleanup",
                                                "after_cleanup", "after_success"} else (OLD if old_exists else None)
                    process = _spawn(path, content_path, stage, "mutable")
                    try:
                        marker = _read_marker(process)
                        if marker != b"READY\n":
                            error = process.stderr.read().decode(errors="replace")
                            self.fail(f"writer missed {stage}: {marker!r}; {error}")
                        observed = path.read_bytes() if path.exists() else None
                        self.assertEqual(observed, expected)
                        self.assertIn(observed, (None, OLD, NEW))
                        process.kill()
                        process.communicate(timeout=5)
                        self.assertEqual(path.read_bytes() if path.exists() else None, expected)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.communicate(timeout=5)

    def test_killed_writer_exposes_only_complete_immutable_facts(self):
        stages = ("before_create", "after_create", "before_write", "partial_write",
                  "after_write", "before_flush", "after_flush", "before_writer_fsync",
                  "after_writer_fsync", "before_link", "after_link",
                  "before_directory_fsync", "after_directory_fsync", "before_cleanup",
                  "after_cleanup", "after_success")
        if os.name == "nt":
            stages = tuple(stage for stage in stages if "directory_fsync" not in stage)
        for old_exists in (False, True):
            eligible = stages if not old_exists else tuple(stage for stage in stages if stage != "after_link")
            for stage in eligible:
                with self.subTest(old_exists=old_exists, stage=stage), tempfile.TemporaryDirectory() as root:
                    path, content_path = Path(root) / "fact.json", Path(root) / "content.bin"
                    content_path.write_bytes(NEW)
                    if old_exists:
                        path.write_bytes(NEW)
                    expected = NEW if old_exists or stage in {"after_link", "before_directory_fsync",
                                                         "after_directory_fsync", "before_cleanup",
                                                         "after_cleanup", "after_success"} else None
                    process = _spawn(path, content_path, stage, "immutable")
                    try:
                        marker = _read_marker(process)
                        if marker != b"READY\n":
                            error = process.stderr.read().decode(errors="replace")
                            self.fail(f"writer missed {stage}: {marker!r}; {error}")
                        self.assertEqual(path.read_bytes() if path.exists() else None, expected)
                        process.kill()
                        process.communicate(timeout=5)
                        self.assertEqual(path.read_bytes() if path.exists() else None, expected)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.communicate(timeout=5)

    def test_immutable_reuses_identical_bytes_and_rejects_different_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "fact.json"
            private_files.publish_bytes(path, OLD, immutable=True)
            original = path.stat()
            private_files.publish_bytes(path, OLD, immutable=True)
            self.assertEqual(path.read_bytes(), OLD)
            self.assertEqual(path.stat().st_ino, original.st_ino)
            with self.assertRaises(FileExistsError):
                private_files.publish_bytes(path, NEW, immutable=True)
            self.assertEqual(path.read_bytes(), OLD)
            self.assertEqual(list(Path(root).glob(".*.tmp")), [])

    def test_killed_conflicting_immutable_writer_keeps_existing_fact(self):
        for stage in ("before_link", "before_cleanup", "after_cleanup"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as root:
                path, content_path = Path(root) / "fact.json", Path(root) / "content.bin"
                path.write_bytes(OLD)
                content_path.write_bytes(NEW)
                process = _spawn(path, content_path, stage, "immutable")
                try:
                    marker = _read_marker(process)
                    if marker != b"READY\n":
                        error = process.stderr.read().decode(errors="replace")
                        self.fail(f"conflicting writer missed {stage}: {marker!r}; {error}")
                    self.assertEqual(path.read_bytes(), OLD)
                    process.kill()
                    process.communicate(timeout=5)
                    self.assertEqual(path.read_bytes(), OLD)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)

    def test_two_immutable_writers_compare_and_create_one_fact(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "fact.json"
            first, second = Path(root) / "first.bin", Path(root) / "second.bin"
            first.write_bytes(OLD)
            second.write_bytes(NEW)
            processes = [_spawn(path, source, "before_link", "immutable")
                         for source in (first, second)]
            try:
                for process in processes:
                    self.assertEqual(_read_marker(process), b"READY\n")
                self.assertFalse(path.exists())
                for process in processes:
                    process.stdin.write(b"1")
                    process.stdin.flush()
                results = []
                for process in processes:
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 0, stderr.decode(errors="replace"))
                    results.append(stdout.strip())
                self.assertCountEqual(results, [b"SUCCESS", b"CONFLICT"])
                self.assertIn(path.read_bytes(), (OLD, NEW))
                self.assertEqual(list(Path(root).glob(".*.tmp")), [])
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)

    def test_writer_fsync_uses_writable_descriptor_and_prepublication_failure_keeps_old(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "fact.json"
            path.write_bytes(OLD)
            real_open, real_fsync = os.open, os.fsync
            writable = set()
            synced = []

            def capture_open(name, flags, *args, **kwargs):
                descriptor = real_open(name, flags, *args, **kwargs)
                if flags & os.O_WRONLY:
                    writable.add(descriptor)
                return descriptor

            def fail_writer_sync(descriptor):
                synced.append(descriptor)
                if descriptor in writable:
                    raise OSError("injected writer fsync failure")
                return real_fsync(descriptor)

            with mock.patch.object(os, "open", side_effect=capture_open), \
                 mock.patch.object(os, "fsync", side_effect=fail_writer_sync):
                with self.assertRaisesRegex(OSError, "writer fsync failure"):
                    private_files.publish_bytes(path, NEW)
            self.assertEqual(len(synced), 1)
            self.assertIn(synced[0], writable)
            self.assertEqual(path.read_bytes(), OLD)
            self.assertEqual(list(Path(root).glob(".*.tmp")), [])

    def test_windows_directory_sync_does_not_open_directory(self):
        with mock.patch.object(private_files.os, "name", "nt"), \
             mock.patch.object(private_files.os, "open", side_effect=AssertionError("directory opened")):
            private_files.sync_directory(Path("unused"))

    def test_existing_json_writers_preserve_their_byte_formats(self):
        from hey_my_buddy.protocol import execution
        from hey_my_buddy.install import launcher
        from hey_my_buddy.blackboard.service import daemon
        from hey_my_buddy.buddy.roles import turn_io
        from hey_my_buddy.json_codec import canonical_json

        value = {"z": "café", "a": [1, True]}
        with tempfile.TemporaryDirectory() as root:
            paths = [Path(root) / name for name in
                     ("execution.json", "launcher.json", "daemon.json", "turn.json")]
            execution.fsync_json(paths[0], value)
            launcher.write_private(paths[1], value)
            daemon.atomic_json(paths[2], value)
            turn_io.private_json(paths[3], value)
            self.assertEqual(paths[0].read_bytes(), json.dumps(value, ensure_ascii=False,
                                                               allow_nan=False).encode("utf-8"))
            self.assertEqual(paths[1].read_bytes(), json.dumps(value).encode("utf-8"))
            self.assertEqual(paths[2].read_bytes(), json.dumps(value, ensure_ascii=False,
                                                               allow_nan=False).encode("utf-8"))
            self.assertEqual(paths[3].read_bytes(), canonical_json(value).encode())


if __name__ == "__main__":
    unittest.main()
