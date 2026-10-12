"""Eight permission observations, using only the Python standard library.

Usage from a checkout: python tests/probes/permission_level_things.py OUTSIDE_DIRECTORY
The existing outside directory must be outside the checkout and platform temporary
directories. This deliberately run probe creates and removes uniquely named files
in the checkout, outside directory and temporary directory, binds a Unix socket
in the checkout and a loopback TCP port, creates shared memory, connects to
pypi.org:443, and starts ps to list processes. Shared memory may start Python's
resource tracker. Each operation prints its observation; the final line is JSON.
Permission denials and other failures retain their reasons separately; successful
cleanup is part of each operation. No harness or repository package is required.
It never calls a model, reads or writes a blackboard, or starts a Buddy service.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path


def temporary_roots() -> set[Path]:
    """Known temporary roots, including aliases resolved through symlinks."""
    roots = {Path(value).resolve() for key in ("TMPDIR", "TEMP", "TMP")
             if (value := os.environ.get(key))}
    if os.name == "posix":
        roots.update(Path(value).resolve() for value in ("/tmp", "/var/tmp", "/usr/tmp"))
    else:
        roots.add((Path.home() / "AppData" / "Local" / "Temp").resolve())
    return roots


def validate_outside(directory: Path, checkout: Path) -> Path:
    directory = directory.expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("OUTSIDE_DIRECTORY must be an existing directory")
    if directory.is_relative_to(checkout) or any(
            directory.is_relative_to(root) for root in temporary_roots()):
        raise ValueError("OUTSIDE_DIRECTORY must be outside the checkout and temporary directories")
    return directory


def observe(action) -> dict:
    try:
        action()
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        denied = isinstance(error, OSError) and error.errno in (errno.EACCES, errno.EPERM)
        reason = f"{type(error).__name__}: {error}"
        if isinstance(error, subprocess.CalledProcessError):
            reason += f"; stderr: {error.stderr}"
        return {"status": "refused" if denied else "failed",
                "reason": reason}
    return {"status": "ok", "reason": "operation and cleanup completed"}


def write_file(directory: Path) -> None:
    path = directory / f"permission-probe-{uuid.uuid4().hex}"
    stream = path.open("x", encoding="utf-8")
    try:
        with stream:
            stream.write("permission probe\n")
    finally:
        path.unlink()


def unix_socket(checkout: Path) -> None:
    if not hasattr(socket, "AF_UNIX"):
        raise NotImplementedError("this platform has no Unix sockets")
    # Relative addressing avoids the Unix socket path limit in long checkouts.
    name = f"pp-{uuid.uuid4().hex[:12]}.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(name)
        try:
            server.listen(1)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(5)
                client.connect(name)
        finally:
            (checkout / name).unlink()


def shared_memory() -> None:
    from multiprocessing.shared_memory import SharedMemory

    block = SharedMemory(create=True, size=4096)
    try:
        block.buf[0] = 1
    finally:
        try:
            block.close()
        finally:
            block.unlink()


def loopback_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=5):
            pass


def network() -> None:
    with socket.create_connection(("pypi.org", 443), timeout=10):
        pass


def list_processes() -> None:
    answer = subprocess.run(["ps", "-e", "-o", "pid="], capture_output=True,
                            text=True, timeout=10, check=True)
    if str(os.getpid()) not in answer.stdout.split():
        raise RuntimeError("ps did not list this probe's process")


def run(outside: Path) -> dict:
    checkout = Path.cwd().resolve()
    actions = {
        "write_checkout": lambda: write_file(checkout),
        "write_outside": lambda: write_file(outside),
        "write_temporary": lambda: write_file(Path(tempfile.gettempdir())),
        "unix_socket": lambda: unix_socket(checkout),
        "shared_memory": shared_memory,
        "loopback_port": loopback_port,
        "network": network,
        "list_processes": list_processes,
    }
    results = {}
    for name, action in actions.items():
        results[name] = observe(action)
        print(f"{name}: {results[name]['status']}: {results[name]['reason']}", flush=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outside_directory", type=Path)
    args = parser.parse_args()
    try:
        outside = validate_outside(args.outside_directory, Path.cwd().resolve())
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(run(outside), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
