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
cooperative surfaces, then waits until every observed survivor has been confirmed to
exit and asserts a clean root: no held Buddy lifetime lock, no live process tied to
the root, and no failed observation. A clean teardown removes the private root; an
incomplete or unobservable one preserves the root together with a written evidence
report. Process observation is strictly read-only (``ps`` argv matching plus
``lsof``/``/proc`` open-file lookup): the runner never signals or kills a process,
never matches or touches a process outside its own private root, and never reads or
prints environment content.
"""
from __future__ import annotations

from . import locking
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
    "BUDDY_CHECKS_TMPDIR",
    "BUDDY_SUPERVISOR_START_ID",
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
    values["BUDDY_CODEX_CLI"] = str(root / "tests/python/fixtures/codex-not-installed")
    # The console backend's fixed default port belongs to the daily service; a private
    # test environment lets the operating system assign its own console ports.
    values["BUDDY_CONSOLE_PORT"] = "0"
    return values


def create_private_root(*, directory: Path | None = None) -> Path:
    """One private root this runner owns and can inspect after the suites exit."""
    root = Path(tempfile.mkdtemp(prefix="buddy-checks-", dir=directory)).resolve()
    (root / "tmp").mkdir(mode=0o700)
    return root


def child_environment(root: Path, private_root: Path) -> dict:
    """The suite environment, with every child temp file inside the private root."""
    values = test_environment(root)
    values["TMPDIR"] = str(private_root / "tmp")
    values["BUDDY_CHECKS_TMPDIR"] = str(private_root / "tmp")
    values['BUDDY_STATE_DIR'] = str(private_root / 'state')
    values['BUDDY_RUNTIME_ROOT'] = str(private_root / 'runtime')
    return values


def lock_is_held(path: Path) -> bool:
    """True when some process still holds this Buddy lifetime lock."""
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        try:
            locking.lock(fd, blocking=False)
        except BlockingIOError:
            return True
        locking.unlock(fd)
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


def _root_prefixes(root: Path) -> tuple[str, ...]:
    """Both spellings of the private root path for argv matching.

    Buddy resolves state directories, so a surviving supervisor's ``--state-dir``
    argv carries the resolved form (macOS aliases ``/var`` through ``/private/var``),
    while this runner may hold the unresolved spelling of the same directory.
    """
    return tuple({str(root), str(root.resolve())})


def _argv_tied(argv: str, prefixes: tuple[str, ...]) -> bool:
    """True when one whole argv token names the root or a path below it."""
    for token in argv.split():
        for prefix in prefixes:
            if token == prefix or token.startswith(prefix + "/"):
                return True
    return False


def _ps_argv_table() -> list[tuple[int, str]] | None:
    """Every process as ``(pid, argv)``, or None when enumeration fails."""
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=", "-o", "args="],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    table: list[tuple[int, str]] = []
    for line in completed.stdout.decode(errors="replace").splitlines():
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        try:
            table.append((int(parts[0]), parts[1] if len(parts) > 1 else ""))
        except ValueError:
            continue
    return table


def _open_file_pids(root: Path) -> tuple[set[int], str | None, bool]:
    """Pids with a file open under the root: ``lsof``, ``/proc`` as the fallback.

    Returns ``(pids, error, available)``. An unavailable channel is a recorded note,
    not a failure; a present channel that fails to run is an error. The name channel includes deleted-but-open files; scanning only existing
    directory entries would miss a daemon after its lock path was unlinked.
    """
    binary = shutil.which("lsof")
    if binary is not None:
        try:
            completed = subprocess.run(
                [binary, "-nP", "-u", str(os.getuid()), "-Fpn"],
                capture_output=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return set(), f"lsof failed: {type(error).__name__}", True
        if completed.returncode == 0:
            stdout = completed.stdout.decode(errors="replace")
            pids = set()
            current_pid = None
            prefixes = _root_prefixes(root)
            for line in stdout.splitlines():
                if line.startswith("p") and line[1:].isdigit():
                    current_pid = int(line[1:])
                elif line.startswith("n") and current_pid is not None:
                    filename = line[1:].removesuffix(" (deleted)")
                    if any(filename == prefix or filename.startswith(prefix + "/") for prefix in prefixes):
                        pids.add(current_pid)
            return pids, None, True
        if completed.returncode == 1 and not completed.stderr.strip():
            # lsof reports "no matching files" as exit 1.
            return set(), None, True
        return set(), f"lsof exited with {completed.returncode}", True
    if Path("/proc").is_dir():
        prefixes = _root_prefixes(root)
        pids: set[int] = set()
        try:
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    handles = list((entry / "fd").iterdir())
                except OSError:
                    continue
                for handle in handles:
                    try:
                        target = os.readlink(handle)
                    except OSError:
                        continue
                    # A deleted open file reads as "<path> (deleted)" and still matches.
                    if any(target == prefix or target.startswith(prefix + "/") for prefix in prefixes):
                        pids.add(int(entry.name))
                        break
            return pids, None, True
        except OSError as error:
            return set(), f"/proc scan failed: {type(error).__name__}", True
    return set(), None, False


def _observation_snapshot(root: Path) -> tuple[dict[int, dict], list[str], list[str]]:
    """One read-only observation pass: findings by pid, fatal problems, notes."""
    problems: list[str] = []
    notes: list[str] = []
    table = _ps_argv_table()
    if table is None:
        return {}, ["ps process enumeration failed; residue status is unknown"], notes
    prefixes = _root_prefixes(root)
    own = os.getpid()
    findings: dict[int, dict] = {}
    argv_by_pid = dict(table)
    for pid, argv in table:
        if pid != own and _argv_tied(argv, prefixes):
            findings[pid] = {"pid": pid, "source": "argv", "executable": Path(argv.split()[0]).name if argv.split() else "unknown"}
    open_pids, open_error, open_available = _open_file_pids(root)
    if not open_available:
        problems.append("open-file observation unavailable (no lsof and no /proc)")
    elif open_error:
        problems.append(open_error)
    for pid in open_pids:
        if pid == own:
            continue
        entry = findings.get(pid)
        if entry is not None:
            entry["source"] = "argv+openFiles"
        else:
            findings[pid] = {"pid": pid, "source": "openFiles",
                             "executable": Path(argv_by_pid.get(pid, "unknown").split()[0]).name}
    return findings, problems, notes


def observed_processes(root: Path) -> tuple[list[dict], list[str], list[str]]:
    """Live processes observably tied to one private root, plus observation status.

    Only processes whose argv names a path under this private root or that hold a
    file under it open are findings — never a process outside the root — and a
    finding must survive a second observation, so a transient indexer passing
    through the root is not residue. No environment content is read or printed and
    reported argv is bounded.
    """
    first, problems, notes = _observation_snapshot(root)
    if problems or not first:
        return sorted(first.values(), key=lambda item: item["pid"]), problems, notes
    time.sleep(0.3)
    second, second_problems, second_notes = _observation_snapshot(root)
    problems.extend(second_problems)
    notes.extend(second_notes)
    if second_problems:
        return sorted(second.values(), key=lambda item: item["pid"]), problems, notes
    stable = [first[pid] for pid in sorted(set(first) & set(second))]
    return stable, problems, notes


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


def request_cooperative_stop(root: Path, timeout: float = 30.0) -> list[str]:
    """Bounded drain: ask observed survivors to stop; never signal a process.

    Waits until no Buddy lifetime lock under the root is held and no live process
    names the root in its argv, so a supervisor whose lock file a racing cleanup
    unlinked still receives its durable stop request and must be observed to exit
    before the caller may treat the root as clean. Returns observation problems; an
    empty list means the drain observed both channels clear.
    """
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
    prefixes = _root_prefixes(root)
    requested: set[Path] = set()
    deadline = time.monotonic() + timeout
    while True:
        table = _ps_argv_table()
        if table is None:
            return ["ps process enumeration failed; residue status is unknown"]
        live = any(pid != os.getpid() and _argv_tied(argv, prefixes) for pid, argv in table)
        residue = held_locks(root)
        if not live and not (residue["daemon"] or residue["supervisor"]):
            return []
        for path in residue["supervisor"]:
            (path.parent / "stop.request").touch(mode=0o600, exist_ok=True)
        for path in residue["daemon"]:
            if path.parent not in requested:
                requested.add(path.parent)
                _request_daemon_stop(path.parent)
        if time.monotonic() >= deadline:
            return []
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

    Runs after every suite, passing or failing. A clean result requires all of: no
    held Buddy lifetime lock under the root, no live process observably tied to the
    root, and no failed observation — an unknown observation status fails
    conservatively. A clean teardown removes the private root and returns None; an
    incomplete or unobservable one preserves the root plus a written evidence report
    and returns the evidence. The drain uses only cooperative stop surfaces and
    read-only observation; no process is ever signalled or killed, and nothing
    outside this private root is read, matched or removed.
    """
    if not root.exists():
        return None
    problems = request_cooperative_stop(root, timeout=drain_seconds)
    residue = held_locks(root)
    processes, observation_problems, notes = observed_processes(root)
    problems = [*problems, *observation_problems]
    evidence: dict = {
        "root": str(root),
        "held": {
            kind: [
                {"path": str(path), "directory": str(path.parent), "directoryFiles": _directory_names(path.parent)}
                for path in paths
            ]
            for kind, paths in residue.items() if paths
        },
        "processes": processes,
        "observation": {"problems": problems, "notes": notes},
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
    clean = (not problems and not processes
             and not residue["daemon"] and not residue["supervisor"])
    if clean:
        try:
            shutil.rmtree(root)
        except OSError as error:
            evidence["cleanupError"] = f"{type(error).__name__}: {error}"
        else:
            return None
    path = _write_evidence(root, evidence)
    evidence["evidencePath"] = str(path)
    findings = []
    if residue["daemon"]:
        findings.append(f"{len(residue['daemon'])} daemon lock(s)")
    if residue["supervisor"]:
        findings.append(f"{len(residue['supervisor'])} supervisor lock(s)")
    if processes:
        findings.append(f"{len(processes)} live process(es)")
    if problems:
        findings.append(f"{len(problems)} observation problem(s)")
    detail = evidence.get("cleanupError") or ", ".join(findings) or "unconfirmed"
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
    # macOS's per-user temporary path leaves too little room for nested native
    # AF_UNIX sockets (sun_path is only 104 bytes). Keep the suite's one owned
    # root short; all child TMPDIRs still stay inside it and share its teardown.
    private_root = create_private_root(directory=Path("/tmp") if sys.platform == "darwin" else None)
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
