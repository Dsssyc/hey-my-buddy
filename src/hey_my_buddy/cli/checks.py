"""Run the Python integration tests and frontend checks in isolated processes.

``python -m hey_my_buddy.cli.checks [--jobs N]`` runs all Python test files and
the console typecheck and unit tests.
``python -m hey_my_buddy.cli.checks [--jobs N] PATH...`` runs only the given
``tests/python/test_*.py`` files or package directories, relative to the checkout
or absolute. On POSIX, ``python -m hey_my_buddy.cli.checks --sweep`` retires
abandoned private roots without running tests; Windows rejects this operation.

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
report. On Windows it holds each suite's creating process handle and a Job
that allows explicit service breakaway, and confirms the root exited and the
held Job is empty. Private services drain through their stop reply and lifetime
locks. A later Windows sweep lacks those handles and is rejected without
changing retained roots or evidence.
On POSIX process observation is strictly read-only (psutil argv/cwd/mapping
observation plus the native file-reference channels psutil cannot fully cover).
The drain uses authenticated stop requests. An interrupted run terminates only
suite children whose creating handles it holds, and ownership setup failures
stop only children they created. Observation never authorizes signals to a
discovered PID, never matches a process outside the private root and never reads
or prints environment content.
"""
from __future__ import annotations

import argparse
import concurrent.futures
from functools import partial
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

from ..text import configure_utf8_output
from .. import locking, private_dirs, state_root
from ..errors import BoardError
from ..paths import native_path
from ..process_observation import observe_open_paths, observe_process_argv
from ..protocol.owned_process import owned_popen
from ..protocol.process_handle import ProcessHandle
from ..protocol.windows_process import WindowsAPI

#: Inherited virtual environment pins not covered by the prefix filter below.
SANITIZED_VARIABLES = (
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)

#: A held lock with one of these names observes a live hey-my-buddy daemon.
DAEMON_LOCK_NAMES = ("control-start.lock", "control-daemon.lock", "board-owner.lock")
EVIDENCE_FILE_NAME = "teardown-evidence.json"
CREATOR_FILE_NAME = "creator.pid"

# These handles belong to this runner, never to a later --sweep process. In
# particular, reopening a vanished, non-kill-on-close Job proves nothing.
_windows_suite_children: dict[Path, list[ProcessHandle]] = {}
_windows_spawn_problems: dict[Path, list[str]] = {}


class _SuiteWindowsAPI(WindowsAPI):
    def create_job(self, name):
        # Services explicitly break away; ordinary descendants remain in this
        # non-kill-on-close Job until their own shutdown is observed.
        return super().create_job(name, limit_flags=self.job.JOB_OBJECT_LIMIT_BREAKAWAY_OK)


def _suite_popen(root: Path, command: list[str], **kwargs):
    if os.name != "nt":
        return subprocess.Popen(command, **kwargs)
    key = root.resolve()
    children = _windows_suite_children.setdefault(key, [])
    try:
        process = owned_popen(command, windows_api=_SuiteWindowsAPI(), **kwargs)
        handle = ProcessHandle(process, own_group=True, log_paths={})
        children.append(handle)
        return process
    except BaseException as error:
        evidence = getattr(error, "owned_popen_evidence", None)
        if evidence is None or not evidence.shutdown_confirmed:
            _windows_spawn_problems.setdefault(key, []).append(
                f"suite creation/shutdown unconfirmed: {type(error).__name__}: {error}")
        raise


def _windows_owned_processes(root: Path) -> tuple[list[dict], list[str], list[str]]:
    key = root.resolve()
    children = _windows_suite_children.get(key)
    if children is None:
        return [], ["owned suite process and Job handles unavailable"], []
    findings = []
    problems = list(_windows_spawn_problems.get(key, []))
    for handle in children:
        try:
            identity = handle.identity()
            # Only a count read on the original held Job can confirm shutdown.
            # OwnedJob's reopen-by-name path is intentionally not used here.
            if handle.job.handle is None:
                raise OSError("creating Job handle is no longer held")
            active = handle.job.active()
            finished = handle.finished
            if active or not finished:
                findings.append({**identity, "source": "ownedJob", "activeProcesses": active,
                                 "rootRunning": not finished})
        except OSError as error:
            problems.append(f"owned suite observation failed: {error}")
    return findings, problems, []


def _release_suite_handles(root: Path) -> None:
    for handle in _windows_suite_children.pop(root.resolve(), []):
        handle.job.close()
    _windows_spawn_problems.pop(root.resolve(), None)


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
    if os.name == "nt":
        _windows_suite_children[root] = []
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
    if os.name == "nt":
        values["TEMP"] = values["TMP"] = values["TMPDIR"]
    values["HMB_CHECKS_TMPDIR"] = str(private_root / "tmp")
    values['HMB_STATE_DIR'] = str(private_root / 'state')
    values['HMB_RUNTIME_ROOT'] = str(private_root / 'runtime')
    return values


#: Upper bound for the derived worker count: every parallel child spawns real
#: daemons, CLIs and harness fixtures of its own, so a conservative cap keeps
#: timing-sensitive tests away from an oversubscribed CPU.
MAX_DEFAULT_JOBS = 4
FRONTEND_LABEL = "apps/console"


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
                    commands: list[list[str]], live: set, guard: threading.Lock,
                    stop: threading.Event, *, cwd: Path | None = None) -> ChildOutcome | None:
    """Run a suite's commands in order inside one private state, runtime and temp root.

    Returns ``None`` without starting anything once the run has been interrupted.
    The child's exit ends the wait: output is collected by reader threads, so a
    detached process that inherited the pipes cannot hold the runner.
    """
    child_root = private_root / directory_name
    environment = child_environment(root, child_root,
                                    home_root=private_root / f"{directory_name}-home")
    started = time.monotonic()
    (child_root / "tmp").mkdir(mode=private_dirs.directory_mode(), parents=True)
    stdout: list[str] = []
    stderr: list[str] = []
    returncode = 0
    for command in commands:
        with guard:
            # The interrupt check, the spawn and the registration share one lock: an
            # interrupted run either never starts this child or already sees it as live.
            if stop.is_set():
                return None
            process = _suite_popen(
                private_root,
                command,
                cwd=str(root if cwd is None else cwd),
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
        output, errors = (b"".join(list(chunks)).decode("utf-8", errors="replace") for chunks in collected)
        stdout.append(output)
        stderr.append(errors)
        returncode = returncode or process.returncode
    return ChildOutcome(label, returncode, "".join(stdout), "".join(stderr), time.monotonic() - started)


def run_frontend_suite(root: Path, private_root: Path, live: set, guard: threading.Lock,
                       stop: threading.Event) -> ChildOutcome | None:
    """One pool job, using the same private environment for both console commands."""
    started = time.monotonic()
    console = root / "apps" / "console"
    node = shutil.which(os.environ.get("HMB_NODE") or "node")
    tsc = console / "node_modules" / "typescript" / "bin" / "tsc"
    vitest = console / "node_modules" / "vitest" / "vitest.mjs"
    if not node or not tsc.is_file() or not vitest.is_file():
        return ChildOutcome(
            FRONTEND_LABEL, 1, "",
            "Frontend checks require Node.js and console dev dependencies; "
            "run `npm --prefix apps/console ci` first.\n", time.monotonic() - started,
        )
    return run_suite_child(
        root, private_root, "frontend", FRONTEND_LABEL,
        [[node, str(tsc), "--noEmit"],
         [node, str(vitest), "run", "--maxWorkers=1", "--reporter=dot"]],
        live, guard, stop, cwd=console,
    )


def _report_child_output(outcome: ChildOutcome) -> None:
    for stream, text in (("stdout", outcome.stdout), ("stderr", outcome.stderr)):
        if text.strip():
            print(f"----- {outcome.label} {stream} -----")
            print(text, end="" if text.endswith("\n") else "\n")


def run_suites_parallel(root: Path, private_root: Path, jobs: int, modules: list[str], *,
                        include_frontend: bool = False) -> str | None:
    """One pool job per Python file, with the full run's frontend job submitted first.

    Every child gets its own private sub-root below the checks root, so no state,
    runtime or temp directory is shared while the suites run; the whole-root teardown
    afterwards still covers them all. Python files retain discovery order.
    A failing child's captured output is printed in full — unittest's verbose
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
    if not modules:
        return "python suite has no test modules"
    frontend = " and 1 frontend suite" if include_frontend else ""
    print(f"hey_my_buddy.cli.checks: {len(modules)} python test files{frontend} across {jobs} workers")
    live: set = set()
    guard = threading.Lock()
    stop = threading.Event()
    tasks = [(module, partial(
        run_suite_child, root, private_root, f"p{index:03d}", module,
        [[sys.executable, "-X", "warn_default_encoding", "-W", "ignore::EncodingWarning",
          "-c", bootstrap, "-v", module]], live, guard, stop,
    )) for index, module in enumerate(modules)]
    if include_frontend:
        tasks.insert(0, (FRONTEND_LABEL, partial(run_frontend_suite, root, private_root, live, guard, stop)))
    outcomes: list[ChildOutcome] = []
    futures: list[concurrent.futures.Future] = []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(jobs, len(tasks)))
    try:
        for _, run in tasks:
            futures.append(pool.submit(run))
        for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            outcome = future.result()
            if outcome is None:
                continue
            outcomes.append(outcome)
            state = "ok" if outcome.returncode == 0 else f"FAILED (exit {outcome.returncode})"
            print(f"[{done}/{len(tasks)}] {outcome.label} {state} {outcome.seconds:.1f}s")
            if outcome.returncode != 0 or outcome.label == FRONTEND_LABEL:
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
    never_ran = sorted(label for label, _ in tasks if label not in reported)
    python_outcomes = [outcome for outcome in outcomes if outcome.label != FRONTEND_LABEL]
    python_failed = sorted(
        outcome.label for outcome in python_outcomes
        if outcome.returncode != 0
    )
    # A parallel run prints only failing children, so the total must stay visible:
    # every passing file reports how many tests it actually ran and skipped.
    ran_total = skipped_total = 0
    uncounted = []
    for outcome in python_outcomes:
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
    python_files = len(modules)
    print(f"hey_my_buddy.cli.checks: python tests run: {ran_total} (skipped {skipped_total}) "
          f"in {python_files - len(python_failed) - len(uncounted)} of {python_files} files")
    failures = []
    if never_ran:
        failures.append(f"{len(never_ran)} suite(s) never ran: {', '.join(never_ran)}")
    if any(outcome.label == FRONTEND_LABEL and outcome.returncode != 0 for outcome in outcomes):
        failures.append(f"frontend suite failed: {FRONTEND_LABEL}")
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
        fd = os.open(native_path(path), os.O_RDWR)
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


def _private_lock_paths(root: Path):
    """Find lifetime lock names without entering links or reparse points."""
    if private_dirs.linked(root):
        return
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(native_path(directory)) as entries:
            children = [directory / entry.name for entry in entries]
        for path in children:
            if private_dirs.linked(path):
                continue
            if path.name in DAEMON_LOCK_NAMES:
                yield path
            if native_path(path).is_dir():
                pending.append(path)


def held_locks(root: Path) -> dict[str, list[Path]]:
    """Held daemon lifetime locks below one private root."""
    residue: dict[str, list[Path]] = {"daemon": []}
    for path in sorted(_private_lock_paths(root)):
        if lock_is_held(path):
            residue["daemon"].append(path)
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
    opened, error, available = observe_open_paths()
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
    """Owned Windows suite trees or POSIX root references, plus observation status.

    Only processes whose argv names a path under this private root or that hold a
    file under it open are findings — never a process outside the root — and a
    finding must survive a second observation, so a transient indexer passing
    through the root is not residue. No environment content is read or printed and
    reported argv is bounded.
    """
    if os.name == "nt":
        return _windows_owned_processes(root)
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


def _request_daemon_stop(state_dir: Path) -> bool:
    """Ask one observed daemon to stop through its own authenticated endpoint."""
    from ..protocol.transport import ServiceError, _read_endpoint, _request

    import c_two as cc

    endpoint = _read_endpoint(state_dir)
    if endpoint is None:
        return False
    try:
        reply = _request(endpoint, "service_control", {"action": "stop", "drainSeconds": 10}, state_dir=state_dir)
        return (bool(reply.get("stopped")) and not reply.get("unresolvedAttempts")) if os.name == "nt" else True
    except ServiceError:
        # The endpoint may already be disappearing; the lifetime locks decide.
        return False
    finally:
        # This runner owns only this short stop RPC. End it before the next
        # observed state root is selected, without touching another native domain.
        if not cc.shutdown().get("completed"):
            raise RuntimeError("Private teardown RPC shutdown is unconfirmed")


def request_cooperative_stop(root: Path, timeout: float = 30.0) -> list[str]:
    """Bounded drain: ask observed survivors to stop; never signal a process.

    Waits for released daemon lifetime locks and, on Windows, exited suite roots
    and empty held Jobs; POSIX observes processes naming the root in argv. Each
    private state is asked to drain once. Windows also requires its confirmed
    stop reply. Returns observation and unconfirmed-drain problems.
    """
    prefixes = _root_prefixes(root)
    requested: set[Path] = set()
    # A suite removes its private service state only after confirming its drain.
    # Leftover daemon locks also cover a suite that lost the reply and then
    # exited: released locks alone cannot prove its detached executions stopped.
    unconfirmed: set[Path] = set()
    deadline = time.monotonic() + timeout
    while True:
        if os.name == "nt":
            processes, problems, _ = _windows_owned_processes(root)
            live = bool(processes)
            unconfirmed.update(path.parent for path in _private_lock_paths(root)
                               if path.name == "control-daemon.lock" and path.parent not in requested)
        else:
            table = _ps_argv_table()
            if table is None:
                return ["psutil process enumeration incomplete; residue status is unknown"]
            live = any(pid != os.getpid() and _argv_tied(argv, prefixes) for pid, argv in table)
            problems = []
        residue = held_locks(root)
        if not live and not residue["daemon"]:
            return [*problems, *(f"private service stop reply unconfirmed: {state}" for state in sorted(unconfirmed))]
        for path in residue["daemon"]:
            if path.parent not in requested:
                requested.add(path.parent)
                if os.name == "nt":
                    unconfirmed.add(path.parent)
                    if _request_daemon_stop(path.parent):
                        unconfirmed.discard(path.parent)
                else:
                    _request_daemon_stop(path.parent)
        if time.monotonic() >= deadline:
            return [*problems, *(f"private service stop reply unconfirmed: {state}" for state in sorted(unconfirmed))]
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


def remove_private_test_root(directory: Path) -> None:
    """Remove a stopped fixture without changing attributes shared by hardlinks.

    The caller must first close its resources and confirm its work has stopped.
    A sharing violation or any other deletion failure preserves the remaining tree.
    """
    directory = native_path(directory)

    def readonly_file(operation, name, error):
        if os.name != "nt" or operation is not os.unlink or getattr(error, "winerror", None) != 5:
            raise error
        path = native_path(Path(name))
        # Open only a regular leaf within the private root, without traversing
        # a link or reparse ancestor.
        for part in (path, *path.parents):
            info = part.lstat()
            attributes = getattr(info, "st_file_attributes", 0)
            if stat.S_ISLNK(info.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise error
            if part == path and (not stat.S_ISREG(info.st_mode) or not attributes & stat.FILE_ATTRIBUTE_READONLY):
                raise error
            if part == directory:
                break
        else:
            raise error
        _delete_readonly_test_leaf(path)

    shutil.rmtree(directory, onexc=readonly_file)


def _delete_readonly_test_leaf(path: Path) -> None:
    """Delete this private test link, preserving every other hardlink's attributes."""
    import ctypes
    from ctypes import wintypes
    from ..windows_files import WindowsFileAPI, DELETE, FILE_READ_ATTRIBUTES, OPEN_EXISTING

    api = WindowsFileAPI()
    handle = api._open(native_path(path), DELETE | FILE_READ_ATTRIBUTES, OPEN_EXISTING, share_delete=True)
    try:
        # FileDispositionInfoEx: DELETE | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE.
        flags = wintypes.DWORD(0x1 | 0x2 | 0x10)
        if not api.kernel.SetFileInformationByHandle(int(handle), 21, ctypes.byref(flags), ctypes.sizeof(flags)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        api.close(handle)


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
    try:
        problems = request_cooperative_stop(root, timeout=drain_seconds)
        residue = held_locks(root)
        processes, observation_problems, notes = observed_processes(root)
    finally:
        _release_suite_handles(root)
    problems = [*problems, *observation_problems]
    if os.name == "nt":
        problems = list(dict.fromkeys(problems))
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
            remove_private_test_root(root)
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
        if not stat.S_ISDIR(metadata.st_mode):
            continue
        try:
            state_root.check_state_root(root)
        except BoardError:
            retained += 1
            print(f"hey_my_buddy.cli.checks: retained {root}: outside this account's private boundary")
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
    configure_utf8_output()
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(prog="hey_my_buddy.cli.checks", add_help=False, allow_abbrev=False)
    parser.add_argument("--jobs")
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("paths", nargs="*")
    options = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if options.sweep:
        if options.paths or options.jobs is not None:
            parser.error("--sweep cannot be combined with paths or --jobs")
        if os.name == "nt":
            parser.error("Windows --sweep cannot prove shutdown without the original process and Job handles; "
                         "inspect retained evidence, then remove roots manually")
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
        failure = run_suites_parallel(root, private_root, jobs, modules,
                                      include_frontend=not options.paths)
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
