"""Durable OS identity and conservative recovery evidence for one execution.

A POSIX root records its boot session, PID, creation time and owned process
group; a Windows root records the PID and raw-FILETIME creation time of the
process the service created plus the exclusively created root Job, and reads no
boot identifier. Only the complete recorded identity authorizes the service to
stop an orphan after its execution has ended. An uninspectable identity remains
unknown; a numeric PID or process-group ID alone is never authority.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import os
import signal
import sys
import threading
import time
import uuid
from pathlib import Path

from ..private_files import publish_bytes
from ..errors import BoardError
from ..json_codec import canonical_json, decode_strict_json
from ..private_dirs import open_regular_fd
from .process_start import process_start


COMMAND_STOP_GRACE_SECONDS = 3.0
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
    return Path(private_root) / f"native-process-journal-{pid}-{started_at}.json"


class NativeProcessJournal:
    """Durable creation intents and complete identities owned by one controller."""

    def __init__(self, path: Path, binding: dict):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.state = {"binding": dict(binding), "pending": [], "identities": []}
        self._publish(initial=True)

    def _publish(self, *, initial: bool = False) -> None:
        path = self.path
        publish_bytes(path, canonical_json(self.state).encode(), immutable=initial)

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
    raise OSError(f"unsupported platform: {sys.platform}")


def _creation_time(pid: int) -> int | None:
    return process_start(pid, include_exited=True)


def _started_at(pid: int) -> int | None:
    return process_start(pid, include_exited=False)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


class _SystemSource:
    boot_id = staticmethod(boot_id)
    started_at = staticmethod(_started_at)
    group_alive = staticmethod(_group_alive)

    @staticmethod
    def group_id(pid: int) -> int:
        return os.getpgid(pid)

    @staticmethod
    @contextmanager
    def open_job(name: str):
        from .windows_process import WindowsAPI
        from .owned_process import OwnedJob

        api = WindowsAPI()
        handle = api.open_job(name)
        if handle is None:
            yield None
            return
        job = OwnedJob(api, handle, name)
        try:
            yield job
        finally:
            job.close()

    @staticmethod
    def signal_group(pgid: int, sig: int) -> None:
        os.killpg(pgid, sig)


def validate_process_identity(record: object) -> dict:
    """Validate the one stored form at the RPC/storage boundary.

    The two platforms carry one shape each, told apart by the Job and
    process-group exclusion: a Windows root names its Job and records no boot
    or group; a POSIX root records its boot session and process-group leader.
    """
    if not isinstance(record, dict):
        raise BoardError("INVALID_ARGUMENT", "process identity has an invalid shape")
    windows = set(record) == {"pid", "startedAt", "jobName"}
    posix = set(record) == {"bootId", "pid", "startedAt", "processGroupId", "jobName"}
    if not windows and not posix:
        raise BoardError("INVALID_ARGUMENT", "process identity has an invalid shape")
    if type(record["pid"]) is not int or record["pid"] <= 0:
        raise BoardError("INVALID_ARGUMENT", "process ID must be positive")
    if type(record["startedAt"]) is not int or not 0 < record["startedAt"] < 2**63:
        raise BoardError("INVALID_ARGUMENT", "process creation time is invalid")
    if windows:
        job = record["jobName"]
        if not isinstance(job, str) or not job:
            raise BoardError("INVALID_ARGUMENT", "owned Job name is invalid")
        try:
            prefix = "Local\\HeyMyBuddy-"
            if not job.startswith(prefix) or str(uuid.UUID(job[len(prefix):])) != job[len(prefix):]:
                raise ValueError("invalid Job name")
        except ValueError as error:
            raise BoardError("INVALID_ARGUMENT", "owned Job name is invalid") from error
        return record
    boot = record["bootId"]
    if not isinstance(boot, str) or not boot:
        raise BoardError("INVALID_ARGUMENT", "boot identifier is required")
    try:
        platform, identifier = boot.split(":", 1)
        if platform not in {"linux", "macos"} or str(uuid.UUID(identifier)) != identifier:
            raise ValueError("noncanonical boot identifier")
    except ValueError as error:
        raise BoardError("INVALID_ARGUMENT", "boot identifier is invalid") from error
    pgid, job = record["processGroupId"], record["jobName"]
    if type(pgid) is not int or pgid <= 0 or pgid != record["pid"]:
        raise BoardError("INVALID_ARGUMENT", "owned process-group leader must match the root PID")
    if job is not None:
        raise BoardError("INVALID_ARGUMENT", "exactly one process group or Job is required")
    return record


def capture_process_identity(handle) -> dict:
    """Capture the complete child identity while its creating handle is held."""
    pid = handle.pid
    child_start = handle.process_started_at
    if child_start is None:
        raise OSError("process identity vanished before capture")
    job = handle.job
    if job is not None:
        return {"pid": pid, "startedAt": child_start, "jobName": job.name}
    boot = boot_id()
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
    return {"bootId": boot, "pid": pid, "startedAt": child_start,
            "processGroupId": handle.pgid, "jobName": None}


def _inspect_job_identity(record: dict, source, job) -> str:
    members = job.members() if job is not None else None
    root_start = source.started_at(record["pid"])
    if not members:
        return "unknown" if root_start == record["startedAt"] else "identity-absent"
    if record["pid"] in members and root_start != record["startedAt"]:
        return "unknown"
    return "alive"


def inspect_process_identity(record: dict, *, source=None) -> str:
    """Return boot-changed, identity-absent, alive, or unknown."""
    source = source or _SystemSource()
    try:
        if record["jobName"] is not None:
            with source.open_job(record["jobName"]) as job:
                return _inspect_job_identity(record, source, job)
        if source.boot_id() != record["bootId"]:
            return "boot-changed"
        root_start = source.started_at(record["pid"])
        group_alive = source.group_alive(record["processGroupId"])
        if root_start is None and not group_alive:
            return "identity-absent"
        if root_start is not None and root_start != record["startedAt"]:
            # A recycled root PID is not the old leader. With group members
            # still present, its provenance cannot be proven from the PGID.
            return "unknown" if group_alive else "identity-absent"
        if root_start is None:
            # A bare PGID cannot prove these are the original descendants.
            return "unknown"
        if not group_alive:
            return "unknown"
        if source.group_id(record["pid"]) != record["processGroupId"]:
            return "unknown"
        # The root may exit and its PID be reused during observation.
        return "alive" if source.started_at(record["pid"]) == root_start else "unknown"
    except Exception as error:
        if not _observation_failure(error):
            raise
        return "unknown"


def kill_verified_process_group(record: dict, *, source=None) -> None:
    """Request a hard stop only while the complete recorded identity is alive.

    Sending a signal is not shutdown evidence; the observer must establish absence.
    """
    source = source or _SystemSource()
    if record["jobName"] is not None:
        _stop_windows_job(record, source, settle_seconds=2.0)
        return
    if inspect_process_identity(record, source=source) != "alive":
        return
    source.signal_group(record["processGroupId"], signal.SIGKILL)


def _stop_windows_job(record: dict, source, *, settle_seconds: float) -> str:
    """Keep the original Job open from verification through observed shutdown."""
    try:
        with source.open_job(record["jobName"]) as job:
            observation = _inspect_job_identity(record, source, job)
            if observation != "alive":
                return observation
            stopped = job.terminate(timeout=settle_seconds)
            return ("orphan-stopped" if stopped and source.started_at(record["pid"]) != record["startedAt"]
                    else "unknown")
    except OSError:
        return "unknown"


def verify_job_member(reported: dict, job_name: str, *, source=None) -> bool:
    """Check one progress report against its root Job at the service boundary.

    The reporting process must belong to the attempt's root Job and carry the
    kernel creation time of its own PID; the root itself may be a different
    process of the same Job.
    """
    source = source or _SystemSource()
    try:
        with source.open_job(job_name) as job:
            if job is None or reported["pid"] not in job.members():
                return False
        return source.started_at(reported["pid"]) == reported["startedAt"]
    except OSError:
        return False


def stop_orphan_process(record: dict, *, source=None, settle_seconds: float = 2.0) -> str:
    """Stop an orphan after the caller proved its execution has ended."""
    source = source or _SystemSource()
    if record["jobName"] is not None:
        return _stop_windows_job(record, source, settle_seconds=settle_seconds)
    observation = inspect_process_identity(record, source=source)
    if observation != "alive":
        return observation
    try:
        source.signal_group(record["processGroupId"], signal.SIGTERM)
    except Exception as error:
        if not _observation_failure(error):
            raise
        return "unknown"
    escalated = False
    deadline = time.monotonic() + COMMAND_STOP_GRACE_SECONDS
    while True:
        observation = inspect_process_identity(record, source=source)
        if observation in ("boot-changed", "identity-absent"):
            return "orphan-stopped"
        if time.monotonic() >= deadline:
            if escalated or observation != "alive":
                return "unknown"
            try:
                # Recheck the complete root/group identity directly before escalation.
                if inspect_process_identity(record, source=source) == "alive":
                    source.signal_group(record["processGroupId"], signal.SIGKILL)
                    escalated = True
                    deadline = time.monotonic() + settle_seconds
                    continue
            except Exception as error:
                if not _observation_failure(error):
                    raise
                return "unknown"
            return "unknown"
        time.sleep(0.05)


def _observation_failure(error: Exception) -> bool:
    return isinstance(error, OSError)
