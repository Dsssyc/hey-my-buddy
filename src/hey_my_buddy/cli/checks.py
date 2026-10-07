"""Run the Python integration tests in isolated processes.

The tests exercise *this checkout*: ``src`` and ``tests/python`` go first on
``PYTHONPATH``, and inherited runtime, worker and agent-credential variables are
removed before each child starts.
A Worker-pinned production runtime must never leak into a test subprocess, and
``BUDDY_DEV_SOURCE=1`` alone cannot override one.

Every child runs inside one private checks root: ``TMPDIR`` points into that root, so
the state and runtime roots each suite creates — including its ``TMPDIR`` fixtures —
never leave it. With the default worker count the Python suite runs one subprocess per
test file — each with its own private state, runtime and temp directories below the
checks root; ``--jobs 1`` (or
``BUDDY_CHECKS_JOBS=1``) restores the original single-process serial run. After the
suites finish, passing or failing, the runner asks any
surviving Buddy daemon or supervisor below the private root to stop through its
cooperative surfaces, then waits until every observed survivor has been confirmed to
exit and asserts a clean root: no held Buddy lifetime lock, no live process tied to
the root, and no failed observation. A clean teardown removes the private root; an
incomplete or unobservable one preserves the root together with a written evidence
report. Process observation is strictly read-only (``ps`` argv matching plus
``lsof``/``/proc`` open-file lookup): the teardown never signals or kills a process,
never matches or touches a process outside its own private root, and never reads or
prints environment content. Only an interrupted parallel run stops processes, and
then only the suite children it started itself; whatever those leave behind is the
teardown's to observe.
"""
from __future__ import annotations

from .. import locking
import concurrent.futures
import json
import locale
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
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
    "BUDDY_ACCOUNT_SELECTION",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)

#: A held lock with one of these names observes a live Buddy daemon or supervisor.
DAEMON_LOCK_NAMES = ("control-start.lock", "control-daemon.lock", "board-owner.lock")
SUPERVISOR_LOCK_NAME = "supervisor.lock"
EVIDENCE_FILE_NAME = "teardown-evidence.json"


def test_environment(root: Path) -> dict:
    """The child environment every test suite runs with: checkout first, no pins."""
    from ..buddy.harnesses.claude.config import THIRD_PARTY_OVERRIDE_VARIABLES

    # A Claude Code Host session exports ANTHROPIC_BASE_URL. Claude fixtures model the
    # first-party account explicitly, so an inherited gateway must not decide them.
    removed = {*SANITIZED_VARIABLES, *THIRD_PARTY_OVERRIDE_VARIABLES}
    values = {key: value for key, value in os.environ.items() if key not in removed}
    source = str(root / "src")
    tests = str(root / "tests" / "python")
    inherited = values.get("PYTHONPATH")
    values["PYTHONPATH"] = os.pathsep.join([source, tests] + ([inherited] if inherited else []))
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
    # Model-facts refreshes stay offline in the check suite: the pinned source is a
    # private fixture path inside this root, and a missing one retains the snapshot
    # instead of fetching.
    values['BUDDY_MODEL_FACTS_FILE'] = str(private_root / 'state' / 'model-facts-fixture.json')
    return values


#: Upper bound for the derived worker count: every parallel child spawns real
#: daemons, CLIs and harness fixtures of its own, so a conservative cap keeps
#: timing-sensitive tests away from an oversubscribed CPU.
MAX_DEFAULT_JOBS = 4

#: The test files measured slowest by the recorded full-suite baseline
#: (docs/acceptance/checks-speedup.md), slowest first. Parallel runs schedule them
#: before the alphabetical remainder so the long tail never queues behind a busy
#: worker. The order is a scheduling hint only: it never changes which tests run.
SLOWEST_FILES_FIRST: tuple[str, ...] = (
    "blackboard.tasks.test_workspace_lifecycle",
    "blackboard.tasks.test_workflow_preparation",
    "blackboard.tasks.test_parallel_dispatch",
    "blackboard.store.test_blackboard",
    "blackboard.tasks.test_workflow_real",
    "blackboard.tasks.test_workspace",
    "blackboard.tasks.test_scope_recovery",
    "buddy.runtime.test_run",
    "blackboard.tasks.test_host_workflow_worker",
    "buddy.runtime.test_repair_recovery",
    "blackboard.routing.test_decision",
    "buddy.harnesses.zcode.test_zcode",
    "buddy.harnesses.claude.test_claude",
    "buddy.harnesses.codex.test_codex",
    "console.test_console",
    "protocol.test_rpc_config",
    "install.test_first_install",
    "protocol.test_ctwo_service",
    "blackboard.routing.test_router",
    "buddy.harnesses.zcode.test_zcode_native",
)


def default_jobs() -> int:
    """A conservative parallel worker count derived from the CPU count."""
    return max(1, min(MAX_DEFAULT_JOBS, (os.cpu_count() or 2) - 1))


def resolve_jobs(arguments: list[str]) -> int:
    """The worker count: ``--jobs`` wins over ``BUDDY_CHECKS_JOBS`` over the default."""
    value: str | None = None
    rest = list(arguments)
    while rest:
        token = rest.pop(0)
        if token == "--jobs":
            if not rest:
                raise SystemExit("hey_my_buddy.cli.checks: --jobs requires a value")
            value = rest.pop(0)
        elif token.startswith("--jobs="):
            value = token.split("=", 1)[1]
        else:
            raise SystemExit(f"hey_my_buddy.cli.checks: unknown argument {token!r}")
    if value is None:
        value = os.environ.get("BUDDY_CHECKS_JOBS")
    if value is None:
        return default_jobs()
    try:
        jobs = int(value)
    except ValueError:
        raise SystemExit(f"hey_my_buddy.cli.checks: invalid jobs value {value!r}") from None
    if jobs < 1:
        raise SystemExit(f"hey_my_buddy.cli.checks: jobs must be at least 1, got {jobs}")
    return jobs


#: How long a finished suite child's output is still collected. A detached process
#: the child left behind keeps the inherited pipes open for as long as it lives; the
#: runner does not wait for it, exactly as a serial run with inherited output never does.
OUTPUT_DRAIN_SECONDS = 2.0

#: How long an interrupted run waits for its terminated suite children before it
#: kills the ones that are still running.
INTERRUPT_GRACE_SECONDS = 10.0

#: Terminal style sequences, which a colour-enabled unittest wraps around its summary.
ANSI_STYLE = re.compile(r"\x1b\[[0-9;]*m")


def python_test_modules(root: Path) -> list[str]:
    """Every Python test module ``unittest discover`` would run, as module names.

    Discovery descends into packages only, so a nested test file is scheduled exactly
    when every directory between it and ``tests/python`` carries an ``__init__.py``.
    """
    top = root / "tests" / "python"
    modules: list[str] = []
    pending = [top]
    while pending:
        directory = pending.pop()
        prefix = ".".join(directory.relative_to(top).parts)
        for path in directory.iterdir():
            if path.is_dir():
                if (path / "__init__.py").is_file():
                    pending.append(path)
            elif path.is_file() and path.name.startswith("test") and path.suffix == ".py":
                modules.append(f"{prefix}.{path.stem}" if prefix else path.stem)
    return sorted(modules)


def _scheduled_test_modules(modules: list[str]) -> list[str]:
    """The baseline-slowest modules first, then the alphabetical remainder."""
    rank = {name: index for index, name in enumerate(SLOWEST_FILES_FIRST)}
    return sorted(modules, key=lambda name: (rank.get(name, len(rank)), name))


class ChildOutcome:
    """One suite child's result: its label, exit code, captured output and time."""

    def __init__(self, label: str, returncode: int, stdout: str, stderr: str, seconds: float):
        self.label = label
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.seconds = seconds


def _collect_output(stream, chunks: list[bytes]) -> None:
    """Read one child pipe until it closes, however long a leftover process holds it."""
    try:
        while chunk := stream.read(65536):
            chunks.append(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def run_suite_child(root: Path, private_root: Path, directory_name: str, label: str,
                    command: list[str], live: set, guard: threading.Lock,
                    stop: threading.Event) -> ChildOutcome | None:
    """Run one suite child inside its own private state, runtime and temp root.

    Returns ``None`` without starting anything once the run has been interrupted.
    The child's exit ends the wait: output is collected by reader threads, so a
    detached process that inherited the pipes cannot hold the runner.
    """
    child_root = private_root / directory_name
    environment = child_environment(root, child_root)
    started = time.monotonic()
    with guard:
        # The interrupt check, the spawn and the registration share one lock: an
        # interrupted run either never starts this child or already sees it as live.
        if stop.is_set():
            return None
        (child_root / "tmp").mkdir(mode=0o700, parents=True)
        process = subprocess.Popen(
            command,
            cwd=str(root),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        live.add(process)
    collected: tuple[list[bytes], list[bytes]] = ([], [])
    readers = [
        threading.Thread(target=_collect_output, args=(stream, chunks), daemon=True)
        for stream, chunks in zip((process.stdout, process.stderr), collected)
    ]
    for reader in readers:
        reader.start()
    try:
        process.wait()
    finally:
        with guard:
            live.discard(process)
    deadline = time.monotonic() + OUTPUT_DRAIN_SECONDS
    for reader in readers:
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
    encoding = locale.getencoding()
    stdout, stderr = (b"".join(list(chunks)).decode(encoding, errors="replace") for chunks in collected)
    return ChildOutcome(label, process.returncode, stdout, stderr, time.monotonic() - started)


def _report_child_output(outcome: ChildOutcome) -> None:
    for stream, text in (("stdout", outcome.stdout), ("stderr", outcome.stderr)):
        if text.strip():
            print(f"----- {outcome.label} {stream} -----")
            print(text, end="" if text.endswith("\n") else "\n")


def run_suites_parallel(root: Path, private_root: Path, jobs: int) -> str | None:
    """One private subprocess per Python test file.

    Every child gets its own private sub-root below the checks root, so no state,
    runtime or temp directory is shared while the suites run; the whole-root teardown
    afterwards still covers them all. The baseline-slowest Python files are submitted
    first. A failing child's captured output is printed in full — unittest's verbose
    listing names the module, class and test — and the failure is summarized by file.
    """
    tasks = [(f"p{index:03d}", module, [sys.executable, "-m", "unittest", "-v", module])
             for index, module in enumerate(_scheduled_test_modules(python_test_modules(root)))]
    if not tasks:
        return "python suite has no test modules"
    print(f"hey_my_buddy.cli.checks: {len(tasks)} python test files across {jobs} workers")
    live: set = set()
    guard = threading.Lock()
    stop = threading.Event()
    outcomes: list[ChildOutcome] = []
    futures: list[concurrent.futures.Future] = []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(jobs, len(tasks)))
    try:
        for directory_name, label, command in tasks:
            futures.append(pool.submit(
                run_suite_child, root, private_root, directory_name, label, command, live, guard, stop
            ))
        for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            outcome = future.result()
            if outcome is None:
                continue
            outcomes.append(outcome)
            state = "ok" if outcome.returncode == 0 else f"FAILED (exit {outcome.returncode})"
            print(f"[{done}/{len(tasks)}] {outcome.label} {state} {outcome.seconds:.1f}s")
            if outcome.returncode != 0:
                _report_child_output(outcome)
    except BaseException:
        # An interrupted run starts no further suite child and leaves none of its own
        # behind the teardown: queued files are dropped, running children are asked to
        # terminate, and one that outlasts the grace period is killed.
        stop.set()
        for future in futures:
            future.cancel()
        with guard:
            running = list(live)
        for process in running:
            process.terminate()
        deadline = time.monotonic() + INTERRUPT_GRACE_SECONDS
        while time.monotonic() < deadline:
            with guard:
                if not live:
                    break
            time.sleep(0.05)
        with guard:
            running = list(live)
        for process in running:
            process.kill()
        raise
    finally:
        pool.shutdown(wait=True)
    # Every scheduled suite must have reported: a file without an outcome did not run.
    reported = {outcome.label for outcome in outcomes}
    never_ran = sorted(label for _, label, _ in tasks if label not in reported)
    python_failed = sorted(
        outcome.label for outcome in outcomes
        if outcome.returncode != 0
    )
    # A parallel run prints only failing children, so the total must stay visible:
    # every passing file reports how many tests it actually ran and skipped.
    ran_total = skipped_total = 0
    uncounted = []
    for outcome in outcomes:
        if outcome.returncode != 0:
            continue
        summary = ANSI_STYLE.sub("", outcome.stderr)
        ran = re.search(r"^Ran (\d+) tests? in ", summary, re.MULTILINE)
        if ran is None:
            uncounted.append(outcome.label)
            continue
        ran_total += int(ran.group(1))
        skipped = re.search(r"^OK \(.*?skipped=(\d+)", summary, re.MULTILINE)
        skipped_total += int(skipped.group(1)) if skipped else 0
    python_files = len(tasks)
    print(f"hey_my_buddy.cli.checks: python tests run: {ran_total} (skipped {skipped_total}) "
          f"in {python_files - len(python_failed) - len(uncounted)} of {python_files} files")
    failures = []
    if never_ran:
        failures.append(f"{len(never_ran)} suite(s) never ran: {', '.join(never_ran)}")
    if python_failed:
        print("hey_my_buddy.cli.checks: python suite failed in "
              f"{len(python_failed)} file(s): {', '.join(python_failed)}")
        failures.append(f"python suite failed in {len(python_failed)} file(s): {', '.join(python_failed)}")
    if uncounted:
        failures.append(f"python suite reported no test count in {len(uncounted)} file(s): {', '.join(sorted(uncounted))}")
    return "; ".join(failures) or None


def run_suites_serially(root: Path, private_root: Path) -> str | None:
    """One Python discovery process using the same private environment."""
    env = child_environment(root, private_root)
    python = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(root / "tests" / "python"), "-v"],
        cwd=str(root),
        env=env,
    )
    if python.returncode != 0:
        return f"python suite exited with {python.returncode}"
    return None


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
    from ..protocol.transport import ServiceError, _read_endpoint, _request

    endpoint = _read_endpoint(state_dir)
    if endpoint is None:
        return
    try:
        _request(endpoint, "service_control", {"action": "stop", "drainSeconds": 10,
                                               "reason": "hey_my_buddy.cli.checks private teardown"})
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
    print(f"hey_my_buddy.cli.checks: teardown incomplete ({detail}); preserved {root}; evidence: {path}",
          file=sys.stderr)
    return evidence


def _safe_teardown(private_root: Path) -> dict | None:
    try:
        return teardown_private_root(private_root)
    except Exception as error:  # noqa: BLE001 - teardown must never mask the suite result
        print(f"hey_my_buddy.cli.checks: teardown itself failed ({type(error).__name__}: {error}); "
              f"preserved {private_root}", file=sys.stderr)
        return {"root": str(private_root), "teardownError": f"{type(error).__name__}: {error}"}


def main(argv: list[str] | None = None) -> None:
    root = Path(__file__).resolve().parents[3]
    jobs = resolve_jobs(sys.argv[1:] if argv is None else argv)
    # macOS's per-user temporary path leaves too little room for nested native
    # AF_UNIX sockets (sun_path is only 104 bytes). Keep the suite's one owned
    # root short; all child TMPDIRs still stay inside it and share its teardown.
    private_root = create_private_root(directory=Path("/tmp") if sys.platform == "darwin" else None)
    print(f"hey_my_buddy.cli.checks: private test root {private_root}", file=sys.stderr)
    failure: str | None = None
    evidence: dict | None = None
    try:
        if jobs > 1:
            failure = run_suites_parallel(root, private_root, jobs)
        else:
            failure = run_suites_serially(root, private_root)
    finally:
        # The residue assertion runs after a failing suite too; a failure here is
        # reported alongside the suite result instead of replacing it.
        evidence = _safe_teardown(private_root)
    if failure or evidence is not None:
        details = [failure] if failure else []
        if evidence is not None:
            details.append("private-root teardown incomplete; evidence preserved (see stderr)")
        raise SystemExit("hey_my_buddy.cli.checks failed: " + "; ".join(details))


if __name__ == "__main__":
    main()
