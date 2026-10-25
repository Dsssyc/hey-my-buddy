"""Interruption and byte-contract checks for private fact publication."""
from __future__ import annotations

import os
import queue
import site
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy import private_files, private_dirs
from hey_my_buddy.paths import native_path


OLD = b'{"generation":"old","complete":true}\n'
NEW = (b'{"generation":"new","payload":"' + b"0123456789abcdef" * 4096
       + b'","complete":true}\n')

# This runs in a separate writer process. The parent stops it while a real file
# operation is in progress, rather than raising an exception in the same process.
# Binary stdout keeps the marker bytes identical across Windows and POSIX.
WRITER = r'''
import os, stat, sys
from pathlib import Path
from hey_my_buddy import private_files

path, stage, mode, content_path = sys.argv[1:]
content = Path(content_path).read_bytes()
def hold(name):
    if stage == name:
        sys.stdout.buffer.write(b"READY\n")
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

if os.name == "nt":
    from hey_my_buddy import windows_files
    real_windows_open = windows_files.WindowsFileAPI._open
    def intercept_windows_open(self, path, access, disposition, **kwargs):
        creating = disposition == windows_files.CREATE_NEW
        if creating: hold("before_create")
        result = real_windows_open(self, path, access, disposition, **kwargs)
        if creating: hold("after_create")
        return result
    windows_files.WindowsFileAPI._open = intercept_windows_open
    real_windows_rename = windows_files.WindowsFileAPI.rename
    def intercept_windows_rename(self, descriptor, target):
        hold("before_replace")
        result = real_windows_rename(self, descriptor, target)
        hold("after_replace")
        return result
    windows_files.WindowsFileAPI.rename = intercept_windows_rename
os.open, os.fdopen = intercept_open, intercept_fdopen
os.fsync, os.replace = intercept_fsync, intercept_replace
os.link, os.unlink = intercept_link, intercept_unlink
try:
    private_files.publish_bytes(Path(path), content, immutable=(mode == "immutable"))
except FileExistsError:
    sys.stdout.buffer.write(b"CONFLICT\n")
else:
    hold("after_success")
    sys.stdout.buffer.write(b"SUCCESS\n")
sys.stdout.flush()
'''


def _wait_for_writer(process: subprocess.Popen, stage: str) -> None:
    messages: queue.Queue[bytes] = queue.Queue()
    thread = threading.Thread(target=lambda: messages.put(process.stdout.readline()), daemon=True)
    thread.start()
    try:
        marker = messages.get(timeout=10)
    except queue.Empty:
        marker = None
    if marker != b"READY\n":
        # A held writer keeps stderr open until it is stopped. Reading stderr
        # before stopping it would deadlock on a malformed readiness marker.
        if process.poll() is None:
            process.kill()
        _, stderr = process.communicate(timeout=5)
        raise AssertionError(f"writer missed {stage}: {marker!r}; {stderr.decode(errors='replace')}")


def _spawn(path: Path, content_path: Path, stage: str, mode: str) -> subprocess.Popen:
    environment = {key: value for key, value in os.environ.items() if key not in {
        "HMB_STATE_DIR", "HMB_RUNTIME_ROOT", "HMB_RUNTIME", "HMB_RUNTIME_IDENTITY",
        "HMB_AGENT_CREDENTIAL_FILE", "HMB_AGENT_CREDENTIAL_FD", "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT"}}
    # Run the actual writer directly, while loading the same locked dependencies
    # as this test environment (including pywin32's .pth bootstrap on Windows).
    bootstrap = "import site; " + "; ".join(f"site.addsitedir({value!r})" for value in site.getsitepackages()) + "\n"
    return subprocess.Popen([sys._base_executable, "-c", bootstrap + WRITER, str(path), stage, mode,
                             str(content_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=environment)


class PrivatePublicationTests(unittest.TestCase):
    def test_long_publication_and_execution_facts_keep_ordinary_references(self):
        from hey_my_buddy.protocol import execution
        from hey_my_buddy.buddy.runtime import receipts
        temporary = private_dirs.make_temporary_dir(prefix='long-facts-', directory=Path(tempfile.gettempdir()))
        self.addCleanup(private_dirs.remove_tree, temporary)
        root = Path(temporary)
        while len(str(root)) < 300:
            root /= 'private-facts-' + 'a' * 35
        directory = private_dirs.ensure_private_dir(root)
        mutable = directory / 'mutable.json'
        private_files.publish_bytes(mutable, OLD)
        with private_files.open_read(mutable) as old_reader:
            private_files.publish_bytes(mutable, NEW)
            self.assertEqual(old_reader.read(), OLD)
            self.assertEqual(private_files.read_bytes(mutable), NEW)
        immutable = directory / 'immutable.json'
        private_files.publish_bytes(immutable, OLD, immutable=True)
        private_files.publish_bytes(immutable, OLD, immutable=True)
        with self.assertRaises(FileExistsError):
            private_files.publish_bytes(immutable, NEW, immutable=True)
        self.assertEqual(list(native_path(directory).glob('.*.tmp')), [])
        binding = {'taskId': 'task', 'attemptId': 'attempt', 'generation': 1}
        execution.fsync_json(directory / execution.STARTUP, binding)
        self.assertEqual(execution._private_json(directory / execution.STARTUP), binding)
        receipt = {**binding, 'createdAt': '2026-10-25T00:00:00+00:00', 'report': {}}
        receipts.write_receipt(directory, receipt)
        reference = execution.receipt_reference(directory)
        self.assertEqual(reference['path'], str(directory / execution.RECEIPT))
        self.assertNotIn('\\\\?\\', reference['path'])
        self.assertEqual(execution.read_execution_receipt(directory, binding=binding, sha256=reference['sha256']), receipt)

    def test_spawn_handle_identifies_the_writer_process(self):
        writer = r'''
import os, sys
from pathlib import Path
Path(sys.argv[1]).write_text(str(os.getpid()), encoding="ascii")
sys.stdout.buffer.write(b"READY\n")
sys.stdout.flush()
sys.stdin.buffer.read(1)
'''
        with tempfile.TemporaryDirectory() as root, mock.patch(__name__ + ".WRITER", writer):
            identity = Path(root) / "writer.pid"
            process = _spawn(identity, Path(root) / "content.bin", "identity", "mutable")
            try:
                _wait_for_writer(process, "identity")
                self.assertEqual(int(identity.read_text(encoding="ascii")), process.pid)
            finally:
                process.kill()
                process.communicate(timeout=5)

    def test_unexpected_marker_stops_held_writer_and_reports_stderr(self):
        writer = r'''
import sys
sys.stderr.write("writer diagnostic\n")
sys.stderr.flush()
sys.stdout.buffer.write(b"UNEXPECTED\n")
sys.stdout.flush()
sys.stdin.buffer.read(1)
'''
        with tempfile.TemporaryDirectory() as root, mock.patch(__name__ + ".WRITER", writer):
            process = _spawn(Path(root) / "fact.json", Path(root) / "content.bin",
                             "before_create", "mutable")
            try:
                with self.assertRaisesRegex(AssertionError, "writer missed before_create:.*writer diagnostic"):
                    _wait_for_writer(process, "before_create")
                self.assertIsNotNone(process.returncode)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)

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
                        _wait_for_writer(process, stage)
                        observed = private_files.read_bytes(path) if path.exists() else None
                        self.assertEqual(observed, expected)
                        self.assertIn(observed, (None, OLD, NEW))
                        process.kill()
                        process.communicate(timeout=5)
                        self.assertEqual(private_files.read_bytes(path) if path.exists() else None, expected)
                        if os.name == 'nt' and stage in {'after_writer_fsync', 'before_replace', 'after_replace'}:
                            bootstrap = 'import site; ' + '; '.join(
                                f'site.addsitedir({value!r})' for value in site.getsitepackages()) + '\n'
                            reader = subprocess.run([sys._base_executable, '-c', bootstrap +
                                'from pathlib import Path; import sys; from hey_my_buddy.private_files import read_bytes; '
                                'p=Path(sys.argv[1]); sys.stdout.buffer.write(read_bytes(p) if p.exists() else b"MISSING")',
                                str(path)], capture_output=True, timeout=10)
                            self.assertEqual(reader.returncode, 0, reader.stderr)
                            self.assertEqual(reader.stdout, expected if expected is not None else b'MISSING')
                        if stage in {"after_cleanup", "before_directory_fsync",
                                     "after_directory_fsync", "after_success"}:
                            self.assertEqual(list(Path(root).glob(".*.tmp")), [])
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
                        _wait_for_writer(process, stage)
                        self.assertEqual(path.read_bytes() if path.exists() else None, expected)
                        process.kill()
                        process.communicate(timeout=5)
                        self.assertEqual(path.read_bytes() if path.exists() else None, expected)
                        if stage in {"after_cleanup", "before_directory_fsync",
                                     "after_directory_fsync", "after_success"}:
                            self.assertEqual(list(Path(root).glob(".*.tmp")), [])
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

    def test_failed_mutable_rename_cleans_candidate_and_keeps_original_error(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'fact.json'
            private_files.publish_bytes(path, OLD)
            failure = OSError('injected publication failure')
            if os.name == 'nt':
                from hey_my_buddy.windows_files import WindowsFileAPI
                replacement = mock.patch.object(WindowsFileAPI, 'rename', side_effect=failure)
            else:
                replacement = mock.patch.object(os, 'replace', side_effect=failure)
            with replacement as replace:
                with self.assertRaises(OSError) as raised:
                    private_files.publish_bytes(path, NEW)
                self.assertIs(raised.exception, failure)
                replace.assert_called_once()
            self.assertEqual(private_files.read_bytes(path), OLD)
            candidates = list(Path(root).glob('.*.tmp'))
            self.assertEqual(candidates, [])

    @unittest.skipUnless(os.name == 'nt', 'native Windows publication and sharing')
    def test_windows_publishes_exact_names_with_different_utf16_lengths(self):
        with tempfile.TemporaryDirectory() as root:
            names = ('.runtime-python', 'daemon.json', 'native-process-journal.json', '中文-😀.json')
            for generation in range(25):
                for name in names:
                    path = Path(root) / name
                    content = f'{name}:{generation}'.encode('utf-8')
                    private_files.publish_bytes(path, content)
                    self.assertEqual(private_files.read_bytes(path), content)
            self.assertEqual({path.name for path in Path(root).iterdir()}, set(names))

    @unittest.skipUnless(os.name == 'nt', 'native Windows publication and sharing')
    def test_windows_product_reader_keeps_old_generation_during_replacement(self):
        from hey_my_buddy import private_dirs
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'fact.json'
            private_files.publish_bytes(path, OLD)
            with os.fdopen(private_dirs.open_regular_fd(path, os.O_RDONLY), 'rb') as reader:
                private_files.publish_bytes(path, NEW)
                self.assertEqual(reader.read(), OLD)
                self.assertEqual(private_files.read_bytes(path), NEW)
            with private_files.open_read(path) as reader:
                private_dirs.validate_private_fd(reader.fileno())

    @unittest.skipUnless(os.name == 'nt', 'native Windows errors 32 and 5')
    def test_windows_sharing_and_readonly_failures_clean_candidates_without_retry(self):
        import stat
        from hey_my_buddy import windows_files
        for kind, code in (('sharing', 32), ('readonly', 5)):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as root:
                path = Path(root) / 'fact.json'
                private_files.publish_bytes(path, OLD)
                api = windows_files.WindowsFileAPI()
                reader = api._open(path, windows_files.GENERIC_READ, windows_files.OPEN_EXISTING) if kind == 'sharing' else None
                if kind == 'readonly':
                    path.chmod(stat.S_IREAD)
                try:
                    with self.assertRaises(OSError) as raised:
                        private_files.publish_bytes(path, NEW)
                    self.assertEqual(raised.exception.winerror, code)
                    self.assertEqual(private_files.read_bytes(path), OLD)
                    candidates = list(Path(root).glob('.*.tmp'))
                    self.assertEqual(candidates, [])
                finally:
                    if reader is not None:
                        api.close(reader)
                    if kind == 'readonly':
                        path.chmod(stat.S_IWRITE)
                private_files.publish_bytes(path, NEW)
                self.assertEqual(private_files.read_bytes(path), NEW)

    def test_killed_conflicting_immutable_writer_keeps_existing_fact(self):
        for stage in ("before_link", "before_cleanup", "after_cleanup"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as root:
                path, content_path = Path(root) / "fact.json", Path(root) / "content.bin"
                path.write_bytes(OLD)
                content_path.write_bytes(NEW)
                process = _spawn(path, content_path, stage, "immutable")
                try:
                    _wait_for_writer(process, stage)
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
                    _wait_for_writer(process, "before_link")
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

            if os.name == 'nt':
                from hey_my_buddy.windows_files import WindowsFileAPI
                real_fd = WindowsFileAPI.fd
                def capture_fd(api, handle, flags):
                    descriptor = real_fd(api, handle, flags)
                    if flags & os.O_WRONLY:
                        writable.add(descriptor)
                    return descriptor
                opener = mock.patch.object(WindowsFileAPI, 'fd', capture_fd)
            else:
                opener = mock.patch.object(os, "open", side_effect=capture_open)
            with opener, mock.patch.object(os, "fsync", side_effect=fail_writer_sync):
                with self.assertRaisesRegex(OSError, "writer fsync failure"):
                    private_files.publish_bytes(path, NEW)
            self.assertEqual(len(synced), 1)
            self.assertIn(synced[0], writable)
            self.assertEqual(path.read_bytes(), OLD)
            candidates = list(Path(root).glob('.*.tmp'))
            self.assertEqual(candidates, [])

    def test_windows_directory_sync_does_not_open_directory(self):
        with mock.patch.object(private_files.os, "name", "nt"), \
             mock.patch.object(private_files.os, "open", side_effect=AssertionError("directory opened")):
            private_files.sync_directory(Path("unused"))

    def test_existing_json_writers_preserve_their_byte_formats(self):
        from hey_my_buddy.protocol import execution
        from hey_my_buddy.install import launcher
        from hey_my_buddy.blackboard.service import daemon
        from hey_my_buddy.buddy.roles import turn_io

        value = {"z": "café", "a": [1, True]}
        # Golden bytes from the four writers at d5c908aaaed3cfd66c20822222570a1e267ff9f2.
        # Keep these literals independent of the current JSON encoder options.
        expected = (
            b'{"z": "caf\xc3\xa9", "a": [1, true]}',
            b'{"z": "caf\\u00e9", "a": [1, true]}',
            b'{"z": "caf\xc3\xa9", "a": [1, true]}',
            b'{"a":[1,true],"z":"caf\xc3\xa9"}',
        )
        with tempfile.TemporaryDirectory() as root:
            paths = [Path(root) / name for name in
                     ("execution.json", "launcher.json", "daemon.json", "turn.json")]
            execution.fsync_json(paths[0], value)
            launcher.write_private(paths[1], value)
            daemon.atomic_json(paths[2], value)
            turn_io.private_json(paths[3], value)
            self.assertEqual(tuple(path.read_bytes() for path in paths), expected)


if __name__ == "__main__":
    unittest.main()
