"""Run the focused Python integration tests and the dependency-free Node suite.

Both suites exercise *this checkout*: ``src`` and ``tests/python`` go first on
``PYTHONPATH``, the DSH Node tests run from ``harnesses/dsh/tests``, and inherited
runtime, worker and agent-credential variables are removed before either child starts.
A Worker-pinned production runtime must never leak into a test subprocess, and
``BUDDY_DEV_SOURCE=1`` alone cannot override one.

Every child runs inside one private checks root: ``TMPDIR`` points into that root, so
the state and runtime roots each suite creates — including its ``TMPDIR`` fixtures —
never leave it. After the suites finish, passing or failing, the runner asks any
surviving Buddy daemon or supervisor below the private root to stop through its
cooperative surfaces, then asserts that no Buddy lifetime lock is still held. A clean
teardown removes the private root; an incomplete one preserves the root together with
a written evidence report. The runner never signals or kills a process and never
touches a path outside its own private root.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

#: Inherited variables that would otherwise point a test subprocess at a production
#: runtime, a Worker identity or an agent credential instead of its own private roots.
SANITIZED_VARIABLES = (
    "BUDDY_STATE_DIR",
    "BUDDY_RUNTIME_ROOT",
    "BUDDY_RUNTIME",
    "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE",
    "BUDDY_WORKER_ID",
    "BUDDY_AGENT_CREDENTIAL",
    "BUDDY_AGENT_CREDENTIAL_FILE",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)

#: A held lock with one of these names observes a live Buddy daemon or supervisor.
DAEMON_LOCK_NAMES = ("control-start.lock", "control-daemon.lock", "board-owner.lock")
SUPERVISOR_LOCK_NAME = "supervisor.lock"
EVIDENCE_FILE_NAME = "teardown-evidence.json"


def test_environment(root: Path) -> dict:
    """The child environment every test suite runs with: checkout first, no pins."""
    from .adapters.claude_config import THIRD_PARTY_OVERRIDE_VARIABLES

    # A Claude Code Host session exports ANTHROPIC_BASE_URL. Claude fixtures model the
    # first-party account explicitly, so an inherited gateway must not decide them.
    removed = {*SANITIZED_VARIABLES, *THIRD_PARTY_OVERRIDE_VARIABLES}
    values = {key: value for key, value in os.environ.items() if key not in removed}
    source = str(root / "src")
    tests = str(root / "tests" / "python")
    inherited = values.get("PYTHONPATH")
    values["PYTHONPATH"] = os.pathsep.join([source, tests] + ([inherited] if inherited else []))
    values["BUDDY_PYTHON"] = sys.executable
    # Tests must exercise this checkout, not a stable runtime install.
    values["BUDDY_DEV_SOURCE"] = "1"
    # Claude availability reads native account metadata. Unrelated tests must not
    # launch the user's CLI; Claude fixtures override this sentinel explicitly.
    values["BUDDY_CLAUDE_CLI"] = str(root / "tests/python/fixtures/claude-not-installed")
    # The console backend's fixed default port belongs to the daily service; a private
    # test environment lets the operating system assign its own console ports.
    values["BUDDY_CONSOLE_PORT"] = "0"
    return values


def create_private_root() -> Path:
    """One private root this runner owns and can inspect after the suites exit."""
    root = Path(tempfile.mkdtemp(prefix="buddy-checks-"))
    (root / "tmp").mkdir(mode=0o700)
    return root


def child_environment(root: Path, private_root: Path) -> dict:
    """The suite environment, with every child temp file inside the private root."""
    values = test_environment(root)
    values["TMPDIR"] = str(private_root / "tmp")
    return values


def lock_is_held(path: Path) -> bool:
    """True when some process still holds this Buddy lifetime lock."""
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def held_locks(root: Path) -> dict[str, list[Path]]:
    """Held daemon and supervisor lifetime locks below one private root."""
    residue: dict[str, list[Path]] = {"daemon": [], "supervisor": []}
    for path in sorted(root.rglob("*.lock")):
        if path.name in DAEMON_LOCK_NAMES:
            kind = "daemon"
        elif path.name == SUPERVISOR_LOCK_NAME:
            kind = "supervisor"
        else:
            continue
        if lock_is_held(path):
            residue[kind].append(path)
    return residue


def _request_daemon_stop(state_dir: Path) -> None:
    """Ask one observed daemon to stop through its own authenticated endpoint."""
    from .transport import ServiceError, _read_endpoint, _request

    endpoint = _read_endpoint(state_dir)
    if endpoint is None:
        return
    try:
        _request(endpoint, "service_control", {"action": "stop", "drainSeconds": 10,
                                               "reason": "buddy.checks private teardown"})
    except ServiceError:
        # The endpoint may already be disappearing; the lifetime locks decide.
        pass


def request_cooperative_stop(root: Path, timeout: float = 30.0) -> None:
    """Bounded drain: ask observed survivors to stop; never signal a process."""
    # A supervisor whose lock file was unlinked by a racing cleanup still owns its
    # flock and keeps recreating its worker directory, so the durable stop request
    # must also reach worker directories that show no held lock.
    for workers_dir in root.rglob("workers"):
        try:
            candidates = [worker for worker in workers_dir.iterdir() if worker.is_dir()]
        except OSError:
            continue
        for worker in candidates:
            (worker / "stop.request").touch(mode=0o600, exist_ok=True)
    requested: set[Path] = set()
    deadline = time.monotonic() + timeout
    while True:
        residue = held_locks(root)
        if not (residue["daemon"] or residue["supervisor"]):
            return
        for path in residue["supervisor"]:
            (path.parent / "stop.request").touch(mode=0o600, exist_ok=True)
        for path in residue["daemon"]:
            if path.parent not in requested:
                requested.add(path.parent)
                _request_daemon_stop(path.parent)
        if time.monotonic() >= deadline:
            return
        time.sleep(0.1)


def _state_directory(path: Path) -> Path:
    # Daemon locks sit at a state root; a supervisor lock sits at
    # <state>/workers/<workerId>/supervisor.lock, three levels below it.
    if path.name == SUPERVISOR_LOCK_NAME:
        return path.parent.parent.parent
    return path.parent


def _directory_names(directory: Path) -> list[str]:
    try:
        return sorted(item.name for item in directory.iterdir())
    except OSError:
        return []


def _log_tail(path: Path, limit: int = 4000) -> str | None:
    try:
        return path.read_text(errors="replace")[-limit:]
    except OSError:
        return None


def _write_evidence(root: Path, evidence: dict) -> Path:
    path = root / EVIDENCE_FILE_NAME
    path.write_text(json.dumps(evidence, indent=2, default=str))
    os.chmod(path, 0o600)
    return path


def teardown_private_root(root: Path, drain_seconds: float = 30.0) -> dict | None:
    """Assert no child daemon or supervisor survives below one private root.

    Runs after every suite, passing or failing. Returns None when teardown completed
    and the private root was removed; otherwise preserves the root plus a written
    evidence report and returns the evidence. The drain uses only cooperative stop
    surfaces and observed lifetime locks; no process is ever signalled or killed, and
    nothing outside this private root is read or removed.
    """
    request_cooperative_stop(root, timeout=drain_seconds)
    residue = held_locks(root)
    evidence: dict = {
        "root": str(root),
        "held": {
            kind: [
                {"path": str(path), "directory": str(path.parent), "directoryFiles": _directory_names(path.parent)}
                for path in paths
            ]
            for kind, paths in residue.items() if paths
        },
    }
    directories = evidence.setdefault("stateDirectories", {})
    for path in (*residue["daemon"], *residue["supervisor"]):
        state = _state_directory(path)
        if str(state) in directories:
            continue
        directories[str(state)] = {
            "files": _directory_names(state),
            "workerLogTail": _log_tail(state / "worker.log"),
            "controlLogTail": _log_tail(state / "control.log"),
        }
    if not (residue["daemon"] or residue["supervisor"]):
        try:
            shutil.rmtree(root)
        except OSError as error:
            evidence["cleanupError"] = f"{type(error).__name__}: {error}"
        else:
            return None
    path = _write_evidence(root, evidence)
    evidence["evidencePath"] = str(path)
    counts = ", ".join(f"{len(paths)} {kind}" for kind, paths in residue.items() if paths)
    detail = evidence.get("cleanupError") or f"{counts} lifetime lock(s) still held"
    print(f"buddy.checks: teardown incomplete ({detail}); preserved {root}; evidence: {path}",
          file=sys.stderr)
    return evidence


def _safe_teardown(private_root: Path) -> dict | None:
    try:
        return teardown_private_root(private_root)
    except Exception as error:  # noqa: BLE001 - teardown must never mask the suite result
        print(f"buddy.checks: teardown itself failed ({type(error).__name__}: {error}); "
              f"preserved {private_root}", file=sys.stderr)
        return {"root": str(private_root), "teardownError": f"{type(error).__name__}: {error}"}


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    private_root = create_private_root()
    print(f"buddy.checks: private test root {private_root}", file=sys.stderr)
    failure: str | None = None
    evidence: dict | None = None
    try:
        env = child_environment(root, private_root)
        python = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(root / "tests" / "python"), "-v"],
            cwd=str(root),
            env=env,
        )
        if python.returncode != 0:
            failure = f"python suite exited with {python.returncode}"
        else:
            node = env.get("BUDDY_NODE") or shutil.which("node")
            if not node:
                raise SystemExit("Node.js is required for the dsh process runner")
            # Browser tests belong to Vitest; only the DSH Node suites under harnesses/dsh are
            # run here, and their support fixtures are not discovered as tests.
            node_tests = sorted(str(path) for path in (root / "harnesses" / "dsh" / "tests").glob("*.test.mjs"))
            result = subprocess.run([node, "--test", *node_tests], cwd=str(root), env=env)
            if result.returncode != 0:
                failure = f"node suite exited with {result.returncode}"
    finally:
        # The residue assertion runs after a failing suite too; a failure here is
        # reported alongside the suite result instead of replacing it.
        evidence = _safe_teardown(private_root)
    if failure or evidence is not None:
        details = [failure] if failure else []
        if evidence is not None:
            details.append("private-root teardown incomplete; evidence preserved (see stderr)")
        raise SystemExit("buddy.checks failed: " + "; ".join(details))


if __name__ == "__main__":
    main()
