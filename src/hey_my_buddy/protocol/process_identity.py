"""Durable OS identity and conservative recovery evidence for one execution.

Only a matching boot, root PID plus creation time, and owned group or Job
authorizes a replacement Worker to stop an orphan.  An uninspectable identity
remains unknown; a numeric PID or process-group ID alone is never authority.
"""
from __future__ import annotations

import ctypes
import math
import os
import signal
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from ..errors import BoardError
from ..json_codec import canonical_json, decode_strict_json
from ..private_dirs import linked_component, open_regular_fd


_active_native_journal = None


def native_process_journal_path(private_root: Path, root_identity: dict | None = None) -> Path:
    """Give each controller invocation its own immutable native spawn journal."""
    if root_identity is None:
        pid = os.getpid()
        started_at = _creation_time(pid)
        if started_at is None:
            raise OSError("controller identity vanished before journal creation")
    else:
        pid = root_identity["pid"]
        started_at = root_identity["startedAt"]
    return Path(private_root) / f"native-process-journal-{pid}-{float(started_at).hex()}.json"


class NativeProcessJournal:
    """Durable creation intents and complete identities owned by one controller."""

    def __init__(self, path: Path, binding: dict):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.state = {"binding": dict(binding), "pending": [], "identities": []}
        self._publish(initial=True)

    def _publish(self, *, initial: bool = False) -> None:
        path = self.path
        if linked_component(path) is not None:
            raise BoardError("PRIVATE_PATH_UNSAFE", "Native process journal contains a linked component")
        raw = canonical_json(self.state).encode()
        if initial:
            fd = open_regular_fd(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            fd, temporary = tempfile.mkstemp(prefix=".native-process-", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def begin(self) -> str:
        with self.lock:
            token = uuid.uuid4().hex
            self.state["pending"].append(token)
            self._publish()
            return token

    def record(self, token: str, handle) -> None:
        with self.lock:
            record = capture_process_identity(handle)
            self.state["identities"].append(record)
            self.state["pending"].remove(token)
            self._publish()

    def discard_confirmed(self, token: str) -> None:
        with self.lock:
            if token in self.state["pending"]:
                self.state["pending"].remove(token)
                self._publish()


def install_native_process_journal(path: Path, binding: dict) -> None:
    """Enable journaling in the controller before it can create native work."""
    global _active_native_journal
    if _active_native_journal is not None:
        raise RuntimeError("native process journal is already installed")
    _active_native_journal = NativeProcessJournal(path, binding)


def begin_native_process_spawn() -> str | None:
    journal = _active_native_journal
    return journal.begin() if journal is not None else None


def native_process_journal_active() -> bool:
    return _active_native_journal is not None


def record_native_process_spawn(token: str | None, handle) -> None:
    if token is not None:
        _active_native_journal.record(token, handle)


def discard_confirmed_native_spawn(token: str | None) -> None:
    if token is not None:
        _active_native_journal.discard_confirmed(token)


def read_native_process_journal(path: Path, binding: dict) -> dict | None:
    """Return valid stable evidence, or None when completeness cannot be proved."""
    try:
        fd = open_regular_fd(path, os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return None
        value = decode_strict_json(raw)
        if (not isinstance(value, dict) or set(value) != {"binding", "pending", "identities"}
                or value["binding"] != binding
                or not isinstance(value["pending"], list)
                or any(not isinstance(item, str) or len(item) != 32 for item in value["pending"])
                or len(set(value["pending"])) != len(value["pending"])
                or not isinstance(value["identities"], list)):
            return None
        for record in value["identities"]:
            validate_process_identity(record)
        return {"identities": value["identities"], "pending": len(value["pending"])}
    except (OSError, ValueError, BoardError, RecursionError):
        return None


def boot_started_at() -> float:
    import psutil

    try:
        return psutil.boot_time()
    except psutil.Error as error:
        raise OSError('boot time observation failed') from error


def boot_id() -> str:
    """Read the OS's stable per-boot identifier, not its adjustable wall clock."""
    if sys.platform == "linux":
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as source:
            value = source.read().strip()
        return f"linux:{uuid.UUID(value)}"
    if sys.platform == "darwin":
        library = ctypes.CDLL(None, use_errno=True)
        method = library.sysctlbyname
        method.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p,
                           ctypes.c_size_t]
        method.restype = ctypes.c_int
        length = ctypes.c_size_t(0)
        if method(b"kern.bootsessionuuid", None, ctypes.byref(length), None, 0) != 0:
            raise OSError(ctypes.get_errno(), "cannot read boot session UUID")
        value = ctypes.create_string_buffer(length.value)
        if method(b"kern.bootsessionuuid", value, ctypes.byref(length), None, 0) != 0:
            raise OSError(ctypes.get_errno(), "cannot read boot session UUID")
        return f"macos:{uuid.UUID(value.value.decode('ascii'))}"
    if sys.platform == "win32":
        # SystemBootEnvironmentInformation's first field is BootIdentifier.
        # Query only that documented-size prefix; failure yields unknown evidence.
        buffer = ctypes.create_string_buffer(32)
        library = ctypes.WinDLL("ntdll")
        query = library.NtQuerySystemInformation
        query.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
        query.restype = ctypes.c_long
        status = query(0x5A, buffer, ctypes.sizeof(buffer), None)
        if status != 0:
            raise OSError(f"cannot read Windows boot identifier: NTSTATUS {status:#x}")
        return f"windows:{uuid.UUID(bytes_le=buffer.raw[:16])}"
    raise OSError(f"unsupported platform: {sys.platform}")


def _creation_time(pid: int) -> float | None:
    import psutil

    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def _started_at(pid: int) -> float | None:
    import psutil

    try:
        process = psutil.Process(pid)
        if process.status() == psutil.STATUS_ZOMBIE:
            return None
        return process.create_time()
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def _group_members(pgid: int) -> list[int]:
    import psutil

    members = []
    for process in psutil.process_iter():
        try:
            if process.status() != psutil.STATUS_ZOMBIE and os.getpgid(process.pid) == pgid:
                members.append(process.pid)
        except (psutil.NoSuchProcess, psutil.ZombieProcess, ProcessLookupError):
            continue
    return members


class _SystemSource:
    boot_id = staticmethod(boot_id)
    started_at = staticmethod(_started_at)
    group_members = staticmethod(_group_members)

    @staticmethod
    def group_id(pid: int) -> int:
        return os.getpgid(pid)

    @staticmethod
    def job_members(name: str) -> list[int] | None:
        from .windows_process import WindowsAPI

        api = WindowsAPI()
        handle = api.open_job(name)
        if handle is None:
            return None
        try:
            return api.process_ids(handle)
        finally:
            api.close(handle)

    @staticmethod
    def stop_job(name: str) -> None:
        from .windows_process import WindowsAPI

        api = WindowsAPI()
        handle = api.open_job(name)
        if handle is None:
            raise ProcessLookupError(name)
        try:
            api.terminate(handle)
        finally:
            api.close(handle)

    @staticmethod
    def stop_group(pgid: int) -> None:
        os.killpg(pgid, signal.SIGKILL)


def validate_process_identity(record: object) -> dict:
    """Validate the one stored form at the RPC/storage boundary."""
    if not isinstance(record, dict) or set(record) != {
            "bootId", "pid", "startedAt", "processGroupId", "jobName", "worker"}:
        raise BoardError("INVALID_ARGUMENT", "process identity has an invalid shape")
    worker = record["worker"]
    if not isinstance(worker, dict) or set(worker) != {"bootId", "pid", "startedAt"}:
        raise BoardError("INVALID_ARGUMENT", "Worker identity has an invalid shape")
    for subject in (record, worker):
        if not isinstance(subject["bootId"], str) or not subject["bootId"]:
            raise BoardError("INVALID_ARGUMENT", "boot identifier is required")
        try:
            platform, identifier = subject["bootId"].split(":", 1)
            if platform not in {"linux", "macos", "windows"} or str(uuid.UUID(identifier)) != identifier:
                raise ValueError("noncanonical boot identifier")
        except ValueError as error:
            raise BoardError("INVALID_ARGUMENT", "boot identifier is invalid") from error
        if type(subject["pid"]) is not int or subject["pid"] <= 0:
            raise BoardError("INVALID_ARGUMENT", "process ID must be positive")
        if type(subject["startedAt"]) not in (int, float) or not math.isfinite(subject["startedAt"]) \
                or subject["startedAt"] <= 0:
            raise BoardError("INVALID_ARGUMENT", "process creation time is invalid")
    pgid, job = record["processGroupId"], record["jobName"]
    if (type(pgid) is int and pgid > 0 and job is None) == (pgid is None and isinstance(job, str) and bool(job)):
        raise BoardError("INVALID_ARGUMENT", "exactly one process group or Job is required")
    if pgid is not None and pgid != record["pid"]:
        raise BoardError("INVALID_ARGUMENT", "owned process-group leader must match the root PID")
    if job is not None:
        try:
            prefix = "Local\\HeyMyBuddy-"
            if not job.startswith(prefix) or str(uuid.UUID(job[len(prefix):])) != job[len(prefix):]:
                raise ValueError("invalid Job name")
        except ValueError as error:
            raise BoardError("INVALID_ARGUMENT", "owned Job name is invalid") from error
    if record["bootId"] != worker["bootId"]:
        raise BoardError("INVALID_ARGUMENT", "execution and Worker must share a boot")
    return record


def capture_process_identity(handle) -> dict:
    """Capture the child and its creating Worker while both handles are held."""
    boot = boot_id()
    pid = handle.pid
    child_start = handle.process_started_at
    worker_pid = os.getpid()
    worker_start = _creation_time(worker_pid)
    if child_start is None or worker_start is None:
        raise OSError("process identity vanished before capture")
    job = handle.job
    record = {"bootId": boot, "pid": pid, "startedAt": child_start,
              "processGroupId": handle.pgid if job is None else None,
              "jobName": job.name if job is not None else None,
              "worker": {"bootId": boot, "pid": worker_pid, "startedAt": worker_start}}
    if job is None:
        if handle.pgid is None:
            raise OSError("owned process group was not captured at spawn")
        try:
            current_group = os.getpgid(pid)
        except ProcessLookupError:
            # The root may already have exited. ProcessHandle captured its
            # creation time and owned group before callers could reap it.
            pass
        else:
            if current_group != handle.pgid:
                raise OSError("owned process group changed before identity capture")
    return validate_process_identity(record)


def worker_identity_alive(record: dict, *, source=None) -> bool | None:
    """Return None if the original Worker's identity cannot be checked."""
    source = source or _SystemSource()
    worker = record["worker"]
    try:
        current_boot = source.boot_id()
        if current_boot != worker["bootId"]:
            return False
        started = source.started_at(worker["pid"])
        return started == worker["startedAt"] if started is not None else False
    except Exception as error:
        if not _observation_failure(error):
            raise
        return None


def inspect_process_identity(record: dict, *, source=None) -> str:
    """Return boot-changed, identity-absent, alive, or unknown."""
    source = source or _SystemSource()
    try:
        if source.boot_id() != record["bootId"]:
            return "boot-changed"
        if record["jobName"] is not None:
            members = source.job_members(record["jobName"])
            root_start = source.started_at(record["pid"])
            if not members:
                return "unknown" if root_start == record["startedAt"] else "identity-absent"
            if record["pid"] in members and root_start != record["startedAt"]:
                return "unknown"
            return "alive"
        root_start = source.started_at(record["pid"])
        members = source.group_members(record["processGroupId"])
        if root_start is None and not members:
            return "identity-absent"
        if root_start is not None and root_start != record["startedAt"]:
            # A recycled root PID is not the old leader. With group members
            # still present, its provenance cannot be proven from the PGID.
            return "unknown" if members else "identity-absent"
        if root_start is None:
            # A bare PGID cannot prove these are the original descendants.
            return "unknown"
        if not members:
            return "unknown"
        if source.group_id(record["pid"]) != record["processGroupId"]:
            return "unknown"
        # The root may exit and its PID be reused during a process scan.
        return "alive" if source.started_at(record["pid"]) == root_start else "unknown"
    except Exception as error:
        if not _observation_failure(error):
            raise
        return "unknown"


def stop_orphan_process(record: dict, *, source=None, settle_seconds: float = 2.0) -> str:
    """Reverify all evidence immediately, stop the owned tree, then observe it gone."""
    source = source or _SystemSource()
    if worker_identity_alive(record, source=source) is not False:
        return "unknown"
    observation = inspect_process_identity(record, source=source)
    if observation != "alive":
        return observation
    # The second inspection is intentional: the prior read may precede a PID
    # or group transition.  Never signal on a cached lone PID.
    if inspect_process_identity(record, source=source) != "alive":
        return "unknown"
    try:
        if record["jobName"] is not None:
            source.stop_job(record["jobName"])
        else:
            source.stop_group(record["processGroupId"])
    except Exception as error:
        if not _observation_failure(error):
            raise
        return "unknown"
    deadline = time.monotonic() + settle_seconds
    while True:
        observation = inspect_process_identity(record, source=source)
        if observation in ("boot-changed", "identity-absent"):
            return "worker-stopped"
        if time.monotonic() >= deadline:
            return "unknown"
        time.sleep(0.025)


def _observation_failure(error: Exception) -> bool:
    import psutil

    return isinstance(error, (OSError, psutil.Error))
