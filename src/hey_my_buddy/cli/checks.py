"""Run the Python integration tests in isolated processes.

``python -m hey_my_buddy.cli.checks [--jobs N]`` runs all Python test files.
``python -m hey_my_buddy.cli.checks [--jobs N] PATH...`` runs only the given
``tests/python/test_*.py`` files or package directories, relative to the checkout
or absolute. ``python -m hey_my_buddy.cli.checks --sweep`` retires abandoned
private roots without running tests.

Exit codes are 0 for passing tests and complete teardown (or no retained sweep
roots), 1 for failed tests regardless of teardown, 2 for usage errors, and 3 for
passing tests with incomplete teardown (or retained sweep roots).

The tests exercise *this checkout*: ``src`` and ``tests/python`` go first on
``PYTHONPATH``, and inherited runtime and agent-credential variables are
removed before each child starts.
a pinned production runtime must never leak into a test subprocess, and
``HMB_DEV_SOURCE=1`` alone cannot override one.

Every child runs inside one private checks root: ``TMPDIR`` points into that root, so
the state and runtime roots each suite creates — including its ``TMPDIR`` fixtures —
never leave it. With the default worker count the Python suite runs one subprocess per
test file — each with its own private state, runtime and temp directories below the
checks root; ``--jobs 1`` (or
``HMB_CHECKS_JOBS=1``) runs those file subprocesses serially. After the
suites finish, passing or failing, the runner asks any
surviving hey-my-buddy daemon below the private root to stop through its
cooperative surfaces, then waits until every observed survivor has been confirmed to
exit and asserts a clean root: no held hey-my-buddy lifetime lock, no live process tied to
the root, and no failed observation. A clean teardown removes the private root; an
incomplete or unobservable one preserves the root together with a written evidence
report. Process observation is strictly read-only (psutil argv/cwd/mapping
observation plus the native file-reference channels psutil cannot fully cover): the teardown never signals or kills a process,
never matches or touches a process outside its own private root, and never reads or
prints environment content. Only an interrupted parallel run stops processes, and
then only the suite children it started itself; whatever those leave behind is the
teardown's to observe.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time

from .. import locking, private_dirs
from ..blackboard.tasks.storage import observe_open_paths, observe_process_argv

#: Inherited virtual environment pins not covered by the prefix filter below.
SANITIZED_VARIABLES = (
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)

#: A held lock with one of these names observes a live hey-my-buddy daemon.
DAEMON_LOCK_NAMES = ("control-start.lock", "control-daemon.lock", "board-owner.lock")
EVIDENCE_FILE_NAME = "teardown-evidence.json"
CREATOR_FILE_NAME = "creator.pid"


def test_environment(root: Path) -> dict:
    """The child environment every test suite runs with: checkout first, no pins."""
    # Test isolation is independent of the product's native configuration:
    # inherited ownership, credentials and harness flags cannot select a fixture.
    values = {key: value for key, value in os.environ.items() if key not in SANITIZED_VARIABLES and not key.startswith(("HMB_", "ANTHROPIC_", "CLAUDE_CODE_", "C2_", "ZCODE_", "ZAI_", "ZHIPU_", "GLM_", "DSH_", "DEEPSEEK_"))}
    # Resolve uv's caller cache before child_environment replaces the native home.
    if "UV_CACHE_DIR" not in values:
        values["UV_CACHE_DIR"] = subprocess.run(
            ["uv", "cache", "dir"], env=values, capture_output=True, text=True,
            encoding="utf-8", check=True,
        ).stdout.strip()
    source = str(root / "src")
    tests = str(root / "tests" / "python")
    inherited = values.get("PYTHONPATH")
    values["PYTHONPATH"] = os.pathsep.join([source, tests] + ([inherited] if inherited else []))
    # Tests must exercise this checkout, not a stable runtime install.
    values["HMB_DEV_SOURCE"] = "1"
    # Claude availability reads native account metadata. Unrelated tests must not
    # launch the user's CLI; Claude fixtures override this sentinel explicitly.
    values["HMB_CLAUDE_CLI"] = str(root / "tests/python/fixtures/claude-not-installed")
    values["HMB_CODEX_CLI"] = str(root / "tests/python/fixtures/codex-not-installed")
    # The console backend's fixed default port belongs to the daily service; a private
    # test environment lets the operating system assign its own console ports.
    values["HMB_CONSOLE_PORT"] = "0"
    return values


def create_private_root(*, directory: Path | None = None) -> Path:
    """One private root this runner owns and can inspect after the suites exit."""
    parent = private_root_directory() if directory is None else directory
    root = private_dirs.make_temporary_dir(prefix="hey-my-buddy-checks-", directory=parent).resolve()
    (root / CREATOR_FILE_NAME).write_text(f"{os.getpid()}\n", encoding="utf-8", newline="\n")
    (root / "tmp").mkdir(mode=private_dirs.directory_mode())
    return root


def prepare_test_home(home: Path) -> None:
    """Initialize a private home whose Git commands never start background writers."""
    home.mkdir(mode=private_dirs.directory_mode(), parents=True, exist_ok=True)
    config = home / ".gitconfig"
    # A private service may already be reading this home; initialize it only once.
    if not config.exists():
        config.write_text("[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n",
                          encoding="utf-8", newline="\n")


def child_environment(root: Path, private_root: Path, *, home_root: Path) -> dict:
    """The suite's private state and a separately owned native home."""
    values = test_environment(root)
    home = home_root
    prepare_test_home(home)
    values.update({
        "HOME": str(home), "USERPROFILE": str(home),
        "HOMEDRIVE": home.drive, "HOMEPATH": str(home),
        "CODEX_HOME": str(home / ".codex"), "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "DSH_HOME": str(home / ".dsh"), "ZCODE_DATA_BASE_DIR": str(home / ".zcode"),
        "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local/share"),
        "XDG_CACHE_HOME": str(home / ".cache"), "XDG_STATE_HOME": str(home / ".local/state"),
        "XDG_RUNTIME_DIR": str(private_root / "runtime"),
        "APPDATA": str(home / "AppData/Roaming"), "LOCALAPPDATA": str(home / "AppData/Local"),
    })
    values["TMPDIR"] = str(private_root / "tmp")
    values["HMB_CHECKS_TMPDIR"] = str(private_root / "tmp")
    values['HMB_STATE_DIR'] = str(private_root / 'state')
    values['HMB_RUNTIME_ROOT'] = str(private_root / 'runtime')
    return values


#: Upper bound for the derived worker count: every parallel child spawns real
#: daemons, CLIs and harness fixtures of its own, so a conservative cap keeps
#: timing-sensitive tests away from an oversubscribed CPU.
MAX_DEFAULT_JOBS = 4

#: The test files measured slowest by the recorded full-suite baseline
#: (docs/acceptance/check-runner/checks-speedup.md), slowest first. Parallel runs schedule them
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


def resolve_jobs(value: str | None) -> int:
    """The worker count: ``--jobs`` wins over ``HMB_CHECKS_JOBS`` over the default."""
    if value is None:
        value = os.environ.get("HMB_CHECKS_JOBS")
    if value is None:
        return default_jobs()
    try:
        jobs = int(value)
    except ValueError:
        raise ValueError(f"invalid jobs value {value!r}") from None
    if jobs < 1:
        raise ValueError(f"jobs must be at least 1, got {jobs}")
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


def selected_test_modules(root: Path, arguments: list[str]) -> list[str]:
    """Select paths from the same package discovery used for a full run."""
    modules = python_test_modules(root)
    if not arguments:
        return modules
    top = (root / "tests" / "python").resolve()
    files = {top.joinpath(*module.split(".")).with_suffix(".py"): module
             for module in modules}
    selected: set[str] = set()
    for argument in arguments:
        path = (root / argument).resolve()
        if not path.is_relative_to(top):
            raise ValueError(f"{argument!r}: path must be below tests/python")
        if not path.exists():
            raise ValueError(f"{argument!r}: path does not exist")
        matches = {module for file, module in files.items()
                   if file.match("test_*.py")
                   and (file == path or (path.is_dir() and file.is_relative_to(path)))}
        if not matches:
            raise ValueError(f"{argument!r}: path contains no discoverable test_*.py files")
        selected.update(matches)
    return sorted(selected)


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
    environment = child_environment(root, child_root,
                                    home_root=private_root / f"{directory_name}-home")
    started = time.monotonic()
    with guard:
        # The interrupt check, the spawn and the registration share one lock: an
        # interrupted run either never starts this child or already sees it as live.
        if stop.is_set():
            return None
        (child_root / "tmp").mkdir(mode=private_dirs.directory_mode(), parents=True)
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
    stdout, stderr = (b"".join(list(chunks)).decode("utf-8", errors="replace") for chunks in collected)
    return ChildOutcome(label, process.returncode, stdout, stderr, time.monotonic() - started)


def _report_child_output(outcome: ChildOutcome) -> None:
    for stream, text in (("stdout", outcome.stdout), ("stderr", outcome.stderr)):
        if text.strip():
            print(f"----- {outcome.label} {stream} -----")
            print(text, end="" if text.endswith("\n") else "\n")


def run_suites_parallel(root: Path, private_root: Path, jobs: int, modules: list[str]) -> str | None:
    """One private subprocess per Python test file.

    Every child gets its own private sub-root below the checks root, so no state,
    runtime or temp directory is shared while the suites run; the whole-root teardown
    afterwards still covers them all. The baseline-slowest Python files are submitted
    first. A failing child's captured output is printed in full — unittest's verbose
    listing names the module, class and test — and the failure is summarized by file.
    """
    # -W escapes its module argument as an exact name. Use the public warning
    # filter API to cover every hey_my_buddy submodule, while ignoring warnings
    # from test fixtures and third-party packages. Python writes UTF-8 to our
    # binary pipes regardless of the machine's locale.
    bootstrap = ("import runpy, sys, warnings; "
                 "sys.stdout.reconfigure(encoding='utf-8'); "
                 "sys.stderr.reconfigure(encoding='utf-8'); "
                 "warnings.filterwarnings('error', category=EncodingWarning, "
                 "module=r'^hey_my_buddy(?:\\.|$)'); "
                 "runpy.run_module('unittest', run_name='__main__')")
    tasks = [(f"p{index:03d}", module, [sys.executable, "-X", "warn_default_encoding",
              "-W", "ignore::EncodingWarning", "-c", bootstrap, "-v", module])
             for index, module in enumerate(_scheduled_test_modules(modules))]
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


def lock_is_held(path: Path) -> bool:
    """True when some process still holds this hey-my-buddy lifetime lock."""
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
    """Held daemon lifetime locks below one private root."""
    residue: dict[str, list[Path]] = {"daemon": []}
    for path in sorted(root.rglob("*.lock")):
        if path.name in DAEMON_LOCK_NAMES:
            kind = "daemon"
        else:
            continue
        if lock_is_held(path):
            residue[kind].append(path)
    return residue


def _root_prefixes(root: Path) -> tuple[str, ...]:
    """Both spellings of the private root path for argv matching.

    hey-my-buddy resolves state directories, so a surviving process's argv may
    carry the resolved form (macOS aliases ``/var`` through ``/private/var``),
    while this runner may hold the unresolved spelling of the same directory.
    """
    return tuple({str(root), str(root.resolve())})


def _argv_tied(argv: list[str], prefixes: tuple[str, ...]) -> bool:
    """True when one whole argv token names the root or a path below it."""
    for token in argv:
        for prefix in prefixes:
            if token == prefix or token.startswith(prefix + os.sep):
                return True
    return False


def _ps_argv_table() -> list[tuple[int, list[str]]] | None:
    """Every process as ``(pid, argv tokens)``, or None if any read failed."""
    rows, known = observe_process_argv()
    return [(process.pid, argv) for process, argv in rows] if known else None


def _open_file_pids(root: Path) -> tuple[set[int], str | None, bool]:
    """PIDs referencing this root, including deleted-but-open file names.

    The shared observation layer distinguishes incomplete and unavailable
    channels. Neither can prove a private root clear; the teardown decides how
    to record that uncertainty alongside its own argv and lifetime-lock checks.
    """
    opened, error, available = observe_open_paths(proc_fds=True)
    prefixes = _root_prefixes(root)
    pids = {pid for pid, paths in opened.items()
            if any(filename == prefix or filename.startswith(prefix + os.sep)
                   for filename in paths for prefix in prefixes)}
    return pids, error, available


def _observation_snapshot(root: Path) -> tuple[dict[int, dict], list[str], list[str]]:
    """One read-only observation pass: findings by pid, fatal problems, notes."""
    problems: list[str] = []
    notes: list[str] = []
    rows, known = observe_process_argv()
    table = [(process.pid, argv) for process, argv in rows]
    if not known:
        problems.append("psutil process enumeration incomplete; residue status is unknown")
    prefixes = _root_prefixes(root)
    own = os.getpid()
    findings: dict[int, dict] = {}
    argv_by_pid = dict(table)
    for pid, argv in table:
        if pid != own and _argv_tied(argv, prefixes):
            findings[pid] = {"pid": pid, "source": "argv", "executable": Path(argv[0]).name if argv else "unknown"}
    open_pids, open_error, open_available = _open_file_pids(root)
    if not open_available:
        problems.append("open-file observation unavailable (native file-reference channel missing)")
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
                             "executable": Path((argv_by_pid.get(pid) or ["unknown"])[0]).name}
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

    import c_two as cc

    endpoint = _read_endpoint(state_dir)
    if endpoint is None:
        return
    try:
        _request(endpoint, "service_control", {"action": "stop", "drainSeconds": 10}, state_dir=state_dir)
    except ServiceError:
        # The endpoint may already be disappearing; the lifetime locks decide.
        pass
    finally:
        # This runner owns only this short stop RPC. End it before the next
        # observed state root is selected, without touching another native domain.
        if not cc.shutdown().get("completed"):
            raise RuntimeError("Private teardown RPC shutdown is unconfirmed")


def request_cooperative_stop(root: Path, timeout: float = 30.0) -> list[str]:
    """Bounded drain: ask observed survivors to stop; never signal a process.

    Waits until no hey-my-buddy daemon lifetime lock under the root is held and no
    live process names the root in its argv. An execution remains tied to the
    private service, whose stop operation drains it. Returns observation problems; an
    empty list means the drain observed both channels clear.
    """
    prefixes = _root_prefixes(root)
    requested: set[Path] = set()
    deadline = time.monotonic() + timeout
    while True:
        table = _ps_argv_table()
        if table is None:
            return ["psutil process enumeration incomplete; residue status is unknown"]
        live = any(pid != os.getpid() and _argv_tied(argv, prefixes) for pid, argv in table)
        residue = held_locks(root)
        if not live and not residue["daemon"]:
            return []
        for path in residue["daemon"]:
            if path.parent not in requested:
                requested.add(path.parent)
                _request_daemon_stop(path.parent)
        if time.monotonic() >= deadline:
            return []
        time.sleep(0.1)


def _state_directory(path: Path) -> Path:
    return path.parent


def _directory_names(directory: Path) -> list[str]:
    try:
        return sorted(item.name for item in directory.iterdir())
    except OSError:
        return []


def _log_tail(path: Path, limit: int = 4000) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return None


def _write_evidence(root: Path, evidence: dict) -> Path:
    path = root / EVIDENCE_FILE_NAME
    path.write_text(json.dumps(evidence, indent=2, default=str),
                    encoding="utf-8", newline="\n")
    os.chmod(path, 0o600)
    return path


def teardown_private_root(root: Path, drain_seconds: float = 30.0) -> dict | None:
    """Assert no child daemon or execution survives below one private root.

    Runs after every suite, passing or failing. A clean result requires all of: no
    held hey-my-buddy lifetime lock under the root, no live process observably tied to the
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
    for path in residue["daemon"]:
        state = _state_directory(path)
        if str(state) in directories:
            continue
        directories[str(state)] = {
            "files": _directory_names(state),
            "controlLogTail": _log_tail(state / "control.log"),
        }
    clean = not problems and not processes and not residue["daemon"]
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
        evidence = {"root": str(private_root), "teardownError": f"{type(error).__name__}: {error}"}
        _write_evidence(private_root, evidence)
        return evidence


def private_root_directory() -> Path:
    """The shared location for new roots and abandoned-root sweeps."""
    # macOS's per-user temporary path leaves too little room for nested native
    # AF_UNIX sockets (sun_path is only 104 bytes). Keep the suite's one owned
    # root short; all child TMPDIRs still stay inside it and share its teardown.
    temporary = os.environ.get("HMB_CHECKS_TMPDIR")
    return Path(temporary) if temporary else (Path("/tmp") if sys.platform == "darwin" else Path(tempfile.gettempdir()))


def sweep_private_roots(directory: Path) -> int:
    """Retire only this user's abandoned checks directories, never active runs."""
    deleted = skipped = retained = 0
    table = _ps_argv_table()
    pids = {pid for pid, _ in table} if table is not None else None
    for root in sorted(directory.glob("hey-my-buddy-checks-*")):
        metadata = root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            continue
        creator = root / CREATOR_FILE_NAME
        reason = None
        if creator.exists():
            try:
                pid = int(creator.read_text(encoding="utf-8"))
                if pid < 1:
                    raise ValueError("creator pid must be positive")
            except (OSError, ValueError) as error:
                reason = f"creator record unreadable: {error}"
            else:
                if pids is None:
                    reason = "creator status unknown: psutil process enumeration incomplete"
                elif pid in pids:
                    skipped += 1
                    print(f"hey_my_buddy.cli.checks: skipped {root}: creator {pid} is alive")
                    continue
        if reason:
            evidence = {"root": str(root), "observation": {"problems": [reason], "notes": []}}
            _write_evidence(root, evidence)
        else:
            evidence = _safe_teardown(root)
        if evidence is None:
            deleted += 1
            print(f"hey_my_buddy.cli.checks: deleted {root}: teardown complete")
        else:
            retained += 1
            detail = (reason or evidence.get("teardownError") or evidence.get("cleanupError")
                      or "; ".join(evidence.get("observation", {}).get("problems", []))
                      or "live process or held lifetime lock")
            print(f"hey_my_buddy.cli.checks: retained {root}: {detail}")
    print(f"hey_my_buddy.cli.checks: sweep deleted {deleted}, skipped {skipped}, retained {retained}")
    return 3 if retained else 0


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", newline="\n")
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(prog="hey_my_buddy.cli.checks", add_help=False, allow_abbrev=False)
    parser.add_argument("--jobs")
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("paths", nargs="*")
    options = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if options.sweep:
        if options.paths or options.jobs is not None:
            parser.error("--sweep cannot be combined with paths or --jobs")
        raise SystemExit(sweep_private_roots(private_root_directory()))
    try:
        jobs = resolve_jobs(options.jobs)
        modules = selected_test_modules(root, options.paths)
    except ValueError as error:
        parser.error(str(error))
    private_root = create_private_root(directory=private_root_directory())
    print(f"hey_my_buddy.cli.checks: private test root {private_root}", file=sys.stderr)
    failure: str | None = None
    evidence: dict | None = None
    try:
        failure = run_suites_parallel(root, private_root, jobs, modules)
    finally:
        # The residue assertion runs after a failing suite too; a failure here is
        # reported alongside the suite result instead of replacing it.
        evidence = _safe_teardown(private_root)
    cleanup = f"teardown incomplete; preserved {private_root}" if evidence is not None else "private root removed"
    if failure:
        print(f"hey_my_buddy.cli.checks: tests failed: {failure}; {cleanup}; exit 1")
        raise SystemExit(1)
    if evidence is not None:
        print(f"hey_my_buddy.cli.checks: tests passed; {cleanup}; exit 3")
        raise SystemExit(3)
    print("hey_my_buddy.cli.checks: tests passed; private root removed; exit 0")


if __name__ == "__main__":
    main()
