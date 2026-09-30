"""DSH native session storage: only the ungrouped session root is attempt-private.

The runner used to relocate the whole child ``DSH_HOME`` for ungrouped runs,
which also moved the file-backed credentials store and made every real run fail
as MISSING_CREDENTIAL before any model work. The repair moves ONLY the JSONL
session backend's ``root`` through the supported per-run patch overlay, so the
DSH home, credentials store and settings document keep resolving in the owning
harness.

These tests drive the real ``harnesses/dsh/scripts/run.mjs`` with a stub ``dsh``
launcher: no installed harness, no credentials, no model call. The stub records
the exact argv, the child ``DSH_HOME`` and the per-run patch document.

A second class binds the reported session identity to the adapter's validated
turn import: an ungrouped governed run has no capture observer, so the turn
record is the only proven id, and a rejected turn must stay uncaptured.

A third class covers the normalized no-deadline sentinel: ``timeoutSeconds=0``
must reach the runner as ``--timeout 0``, must not stamp an already-expired
adapter deadline, and must stay cancellable through the owned process group.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from buddy.adapters import turn_io
from buddy.adapters.base import ExecutionContext, ProcessHandle
from buddy.adapters.dsh import DshAdapter
from buddy.schemas import normalize_spec

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "harnesses/dsh/scripts/run.mjs"
TASK = "Do the bounded thing.\n"
# Inherited Buddy runtime/worker/credential variables must never leak into a
# test subprocess, and the child must never see a pinned dev environment.
CLEARED_ENV = (
    "BUDDY_STATE_DIR", "BUDDY_RUNTIME_ROOT", "BUDDY_RUNTIME", "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE", "BUDDY_WORKER_ID", "BUDDY_AGENT_CREDENTIAL",
    "BUDDY_AGENT_CREDENTIAL_FILE", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT",
    "DSH_SETTINGS_FILE", "DSH_BIN", "DSH_DELEGATE_MODEL", "DSH_DELEGATE_PROVIDER",
    "DSH_DELEGATE_EFFORT", "DSH_WORKSPACE_SOCKET",
)

STUB = textwrap.dedent(
    """\
    #!/bin/sh
    # Stub dsh launcher: records what the runner actually passed, then exits ok.
    artifact_dir="${MOCK_ARTIFACT_DIR:?}"
    printf '%s\\n' "$@" > "$artifact_dir/argv.txt"
    printf '%s' "${DSH_HOME-}" > "$artifact_dir/dsh-home.txt"
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "--patch" ] && [ "$#" -ge 2 ]; then
        shift
        cp "$1" "$artifact_dir/patch.json"
      fi
      shift
    done
    exit 0
    """
)


class DshWorkspaceDefaultTests(unittest.TestCase):
    def test_grouping_requires_explicit_opt_in_even_when_adapter_changes(self):
        with tempfile.TemporaryDirectory(prefix="buddy-dsh-spec-") as root:
            common = {"requestId": "dsh-default", "task": "bounded task", "cwd": root}
            self.assertFalse(normalize_spec(common)["workspace"])
            self.assertFalse(normalize_spec({**common, "adapter": "dsh"})["workspace"])
            self.assertTrue(normalize_spec({**common, "workspace": True})["workspace"])
            self.assertFalse(normalize_spec({**common, "adapter": "codex"})["workspace"])


@unittest.skipUnless(shutil.which("node") and RUNNER.is_file(), "Node.js and the dsh runner are required")
class DshSessionRootTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-dsh-session-root-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "dsh-home"
        self.home.mkdir()
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        self.task = self.root / "task.md"
        self.task.write_text(TASK)
        self.session_root = self.root / "attempt" / "sessions"
        self.stub = self.root / "dsh"
        self.stub.write_text(STUB)
        self.stub.chmod(0o700)

    def environment(self) -> dict:
        env = {key: value for key, value in os.environ.items() if key not in CLEARED_ENV}
        env["DSH_HOME"] = str(self.home)
        env["MOCK_ARTIFACT_DIR"] = str(self.artifacts)
        return env

    def invoke(self, *flags: str, workspace: bool = False) -> subprocess.CompletedProcess:
        base = [
            shutil.which("node"), str(RUNNER),
            "--cwd", str(self.checkout),
            "--task-file", str(self.task),
            "--dsh-bin", str(self.stub),
            "--log-dir", str(self.root / "logs"),
        ]
        if workspace:
            base.append("--workspace")
        return subprocess.run([*base, *flags], capture_output=True, text=True, timeout=60,
                              env=self.environment())

    def test_ungrouped_run_moves_only_the_session_root_and_keeps_the_owning_home(self):
        completed = self.invoke(f"--session-root={self.session_root}")
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["status"], "ok")

        storage = result["nativeStorage"]
        self.assertEqual(storage["scope"], "task-private-sessions")
        self.assertTrue(storage["sessionRootPrivate"])
        self.assertEqual(storage["credentialsStore"], "harness-user-store")
        self.assertEqual(storage["nativeAppVisibility"], "not-listed-in-native-app")
        self.assertEqual(storage["resumeMode"], "reconstructed-new-session")

        argv = (self.artifacts / "argv.txt").read_text().splitlines()
        self.assertEqual(argv[0], "--profile")
        self.assertFalse(any(argument.startswith("--dsh-home") for argument in argv), argv)
        patch_path = Path(argv[argv.index("--patch") + 1])
        self.assertEqual(patch_path.name, "patch.json")

        # The child inherited the owning DSH home instead of a relocated copy:
        # that is what keeps the file-backed credentials store resolvable.
        self.assertEqual((self.artifacts / "dsh-home.txt").read_text(), str(self.home))

        rows = json.loads((self.artifacts / "patch.json").read_text())
        self.assertEqual(rows[0]["id"], "settings")
        session_row = next(row for row in rows if row.get("id") == "session-persistence-jsonl")
        self.assertEqual(session_row["config"], {"root": str(self.session_root)})
        self.assertEqual([row.get("id") for row in rows if "insert" in row], [])

        self.assertTrue(self.session_root.is_dir())
        self.assertEqual(stat.S_IMODE(self.session_root.stat().st_mode), 0o700)

    def test_default_run_uses_a_private_session_root_without_grouping(self):
        completed = self.invoke()
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["workspace"]["enabled"], False)
        self.assertEqual(result["nativeStorage"]["scope"], "run-private-sessions")
        log_dir = Path(result["logPaths"]["stdout"]).parent
        self.assertTrue((log_dir / "sessions").is_dir())
        rows = json.loads((self.artifacts / "patch.json").read_text())
        session_row = next(row for row in rows if row.get("id") == "session-persistence-jsonl")
        self.assertEqual(session_row["config"], {"root": str(log_dir / "sessions")})

    def test_a_grouped_run_never_gets_a_private_session_root(self):
        completed = self.invoke(f"--session-root={self.session_root}", workspace=True)
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn("cannot be combined with --workspace", completed.stderr)
        self.assertFalse((self.artifacts / "argv.txt").exists(), "the stub dsh must never start")

    def test_the_session_root_must_be_an_absolute_path(self):
        completed = self.invoke("--session-root", "relative-sessions")
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn("absolute path", completed.stderr)
        self.assertFalse((self.artifacts / "argv.txt").exists())

    def test_a_symlinked_session_root_is_refused(self):
        real = self.root / "real-sessions"
        real.mkdir()
        link = self.root / "linked-sessions"
        link.symlink_to(real, target_is_directory=True)
        completed = self.invoke(f"--session-root={link}")
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn("could not create the private session root", completed.stderr)
        self.assertFalse((self.artifacts / "argv.txt").exists())

    def test_a_session_root_that_is_a_file_is_refused(self):
        occupied = self.root / "occupied-sessions"
        occupied.write_text("not a directory\n")
        completed = self.invoke(f"--session-root={occupied}")
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn("could not create the private session root", completed.stderr)
        self.assertFalse((self.artifacts / "argv.txt").exists())

    def test_the_removed_private_dsh_home_flag_is_rejected(self):
        completed = self.invoke(f"--dsh-home={self.root / 'child-home'}")
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn("--dsh-home", completed.stderr)
        self.assertFalse((self.artifacts / "argv.txt").exists())


TURN_INPUT = {
    "version": 1,
    "taskId": "task",
    "attemptId": "attempt",
    "generation": 1,
    "turnId": "turn-1",
    "resumeMode": "initial",
    "previousSessionId": None,
    "context": {},
    "executionWorkspace": {},
}


class NativeSessionBindingTests(unittest.TestCase):
    """The validated turn record binds the id; a rejected turn never invents one."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-dsh-bind-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.attempt = self.root / "attempt"
        self.attempt.mkdir()
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()

    def collect(self, record: dict | None, *, payload: dict | None = None):
        context = ExecutionContext(
            task_id="task", attempt_id="attempt", generation=1,
            spec={"cwd": str(self.checkout), "task": "x", "timeoutSeconds": 30, "workspace": False},
            directory=self.attempt, runtime={},
            environment={"BUDDY_STATE_DIR": str(self.root / "state")},
            turn={"turnId": "turn-1", "input": dict(TURN_INPUT)},
        )
        if record is not None:
            context.turn_output_file().write_text(json.dumps(record))
        stdout = self.attempt / "stdout.log"
        stdout.write_text(json.dumps(payload if payload is not None else {
            "status": "ok",
            "processState": {"shutdownConfirmed": True},
            "logPaths": {"stdout": str(stdout), "capture": None},
            "nativeStorage": {
                "scope": "task-private-sessions", "sessionRootPrivate": True, "credentialsStore": "harness-user-store",
                "nativeAppVisibility": "not-listed-in-native-app", "resumeMode": "reconstructed-new-session",
            },
        }) + "\n")
        process = subprocess.Popen([sys.executable, "-c", ""])
        process.wait()
        handle = ProcessHandle(process, own_group=False, log_paths={"stdout": str(stdout)})
        return DshAdapter().collect(handle, context)

    def record(self, *, session_id: str | None = "native-session-1", root_session_matched: bool = True) -> dict:
        identity = {key: TURN_INPUT[key] for key in
                    ("taskId", "attemptId", "generation", "turnId", "resumeMode", "previousSessionId")}
        value = {
            "version": 1,
            **identity,
            "inputSha256": turn_io.input_hash(TURN_INPUT),
            "outcome": {"disposition": "completed", "summary": "mock turn completed", "remaining": [],
                        "decisions": [], "artifacts": [], "request": None},
            "provenance": {"tool": "buddy_finish_turn", "turnEnd": "completed", "flush": "awaited",
                           "rootSessionMatched": root_session_matched},
        }
        if session_id is not None:
            value["sessionId"] = session_id
        return value

    def test_a_validated_ungrouped_turn_binds_the_session_id(self):
        outcome = self.collect(self.record())
        self.assertEqual(outcome.status, "ok")
        native = outcome.result["nativeSession"]
        self.assertEqual(native["sessionId"], "native-session-1")
        self.assertTrue(native["captured"])
        self.assertEqual(native["sessionIdSource"], "validated-turn")
        self.assertFalse(native["sessionIdConflict"])
        self.assertEqual(native["storageScope"], "task-private-sessions")
        self.assertEqual(native["storageOwner"], "buddy-attempt")
        self.assertEqual(native["credentialsStore"], "harness-user-store")
        self.assertEqual(outcome.result["turn"]["sessionId"], native["sessionId"])

    def test_a_rejected_turn_stays_uncaptured_instead_of_inventing_a_session(self):
        for record in (self.record(root_session_matched=False), self.record(session_id=None)):
            with self.subTest(sessionId=record.get("sessionId"), rootSessionMatched=record["provenance"]["rootSessionMatched"]):
                outcome = self.collect(record)
                self.assertEqual(outcome.status, "failed")
                self.assertIsNotNone(outcome.result.get("turnError"))
                native = outcome.result["nativeSession"]
                self.assertIsNone(native["sessionId"])
                self.assertFalse(native["captured"])
                self.assertEqual(native["sessionIdSource"], "none")

    def test_the_ungoverned_observer_capture_is_still_reported(self):
        context = ExecutionContext(
            task_id="task", attempt_id="attempt", generation=1,
            spec={"cwd": str(self.checkout), "task": "x", "timeoutSeconds": 30, "workspace": True},
            directory=self.attempt, runtime={}, environment={}, turn=None,
        )
        stdout = self.attempt / "stdout.log"
        stdout.write_text(json.dumps({
            "status": "ok", "processState": {"shutdownConfirmed": True},
            "logPaths": {"stdout": str(stdout), "capture": None},
            "workspace": {"enabled": True, "bound": True, "sessionId": "grouped-session-9"},
            "nativeStorage": {"scope": "harness-user-store", "sessionRootPrivate": False},
        }) + "\n")
        process = subprocess.Popen([sys.executable, "-c", ""])
        process.wait()
        outcome = DshAdapter().collect(ProcessHandle(process, own_group=False, log_paths={"stdout": str(stdout)}), context)
        native = outcome.result["nativeSession"]
        self.assertEqual(native["sessionId"], "grouped-session-9")
        self.assertEqual(native["sessionIdSource"], "native-session-observer")
        self.assertEqual(native["storageOwner"], "harness-user-store")


#: Stub dsh that outlives a real delay, then finishes normally. If the adapter or
#: the runner turned ``timeoutSeconds=0`` into an immediate deadline, this child
#: is signalled long before it prints, so the run can never report ok.
DELAY_STUB = textwrap.dedent(
    """\
    #!/bin/sh
    sleep "${MOCK_STUB_SLEEP_SECONDS:-3}"
    printf 'stub dsh completed\\n'
    exit 0
    """
)

#: Stub dsh that leaves a long-lived descendant in its own group. A cancel that
#: signalled only the direct child would orphan that descendant, so its death is
#: the observable proof that the whole owned group was stopped.
HANG_STUB = textwrap.dedent(
    """\
    #!/bin/sh
    artifact_dir="${MOCK_ARTIFACT_DIR:?}"
    sleep 300 &
    printf '%s' "$!" > "$artifact_dir/grandchild.txt"
    wait
    """
)


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@unittest.skipUnless(shutil.which("node") and RUNNER.is_file(), "Node.js and the dsh runner are required")
class DshNoDeadlineSentinelTests(unittest.TestCase):
    """``timeoutSeconds=0`` is a real no-deadline attempt, not an expired one.

    These tests drive the real runner through the production adapter with a stub
    dsh, so the sentinel is proven end to end: it reaches the runner verbatim, no
    adapter deadline is stamped, a zero-duration attempt survives a real delay,
    and it stays cancellable through the owned process group.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bdd-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.checkout = self.root / "cwd"
        self.checkout.mkdir()
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.attempt = self.root / "attempt"
        self.handles: list[ProcessHandle] = []
        overrides = mock.patch.dict(os.environ, {"BUDDY_RUNNER_PATH": str(RUNNER)})
        overrides.start()
        self.addCleanup(overrides.stop)

    def tearDown(self):
        # A failing assertion must never leave a stub or the runner behind.
        for handle in self.handles:
            if handle.process.poll() is None:
                handle.terminate(grace_seconds=1.0)

    def stub(self, script: str) -> Path:
        path = self.root / "dsh"
        path.write_text(script)
        path.chmod(0o700)
        return path

    def environment(self) -> dict:
        env = {key: value for key, value in os.environ.items() if key not in CLEARED_ENV}
        node_binary = shutil.which("node") or ""
        env.update({
            "PATH": os.pathsep.join([str(Path(node_binary).parent), env.get("PATH", "")]),
            "DSH_HOME": str(self.home),
            "MOCK_ARTIFACT_DIR": str(self.artifacts),
            "MOCK_STUB_SLEEP_SECONDS": "3",
            "BUDDY_PYTHON": sys.executable,
            "BUDDY_STATE_DIR": str(self.root / "state"),
            "BUDDY_RUNTIME_ROOT": str(self.root / "runtime"),
            "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests" / "python")]),
        })
        return env

    def context(self, *, timeout_seconds: int, stub: Path, workspace: bool = False) -> ExecutionContext:
        return ExecutionContext(
            task_id="task", attempt_id="attempt", generation=1,
            spec={"cwd": str(self.checkout), "task": TASK, "timeoutSeconds": timeout_seconds, "workspace": workspace},
            directory=self.attempt, runtime={}, environment={**self.environment(), "DSH_BIN": str(stub)}, turn=None,
        )

    def start(self, context: ExecutionContext) -> tuple[DshAdapter, ProcessHandle]:
        adapter = DshAdapter()
        handle = adapter.start(context)
        self.handles.append(handle)
        return adapter, handle

    def wait_for_artifact(self, name: str, timeout: float = 10.0) -> str:
        deadline = time.monotonic() + timeout
        path = self.artifacts / name
        while time.monotonic() < deadline:
            if path.is_file() and path.read_text().strip():
                return path.read_text().strip()
            time.sleep(0.05)
        self.fail(f"the stub dsh never recorded {name}")

    def test_the_runner_receives_the_zero_sentinel_verbatim(self):
        arguments = DshAdapter().arguments(
            self.context(timeout_seconds=0, stub=self.stub(DELAY_STUB)),
            {"socketPath": "/tmp/unused.sock", "token": "0" * 64, "resultsPath": "/tmp/unused.jsonl", "errorPath": "/tmp/unused.error.json"},
        )
        self.assertEqual(arguments[arguments.index("--timeout") + 1], "0")

    def test_a_zero_timeout_attempt_survives_a_real_delay_and_completes(self):
        context = self.context(timeout_seconds=0, stub=self.stub(DELAY_STUB))
        adapter, handle = self.start(context)
        self.assertIsNone(handle.deadline, "timeoutSeconds=0 must not stamp an already-expired deadline")
        time.sleep(0.6)
        self.assertIsNone(handle.process.poll(), "a 0-deadline attempt must still run after a real delay")
        self.assertEqual(handle.wait(timeout=30), 0)

        outcome = adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.error)
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(outcome.result["status"], "ok")
        self.assertEqual(outcome.result["timeoutSeconds"], 0, "the sentinel is reported verbatim")

    def test_a_positive_timeout_still_stamps_a_future_deadline(self):
        context = self.context(timeout_seconds=30, stub=self.stub(DELAY_STUB))
        adapter, handle = self.start(context)
        stamped = handle.deadline
        self.assertIsNotNone(stamped)
        self.assertGreater(stamped, time.monotonic(), "a positive timeout keeps a future deadline")
        self.assertLessEqual(stamped, time.monotonic() + 31)
        self.assertEqual(handle.wait(timeout=30), 0)

        outcome = adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.error)
        self.assertEqual(outcome.result["timeoutSeconds"], 30)

    def test_a_zero_timeout_attempt_stays_cancellable_through_its_owned_group(self):
        context = self.context(timeout_seconds=0, stub=self.stub(HANG_STUB))
        adapter, handle = self.start(context)
        self.assertIsNone(handle.deadline)
        grandchild = int(self.wait_for_artifact("grandchild.txt"))
        self.assertTrue(process_alive(grandchild), "the stub descendant must be running before the cancel")

        adapter.cancel(handle)

        self.assertTrue(handle.cancel_requested)
        self.assertIsNotNone(handle.process.poll(), "cancellation stops the owned runner")
        self.assertTrue(handle.shutdown_confirmed(), "the whole owned group must be confirmed stopped")
        self.assertFalse(process_alive(grandchild), "a descendant of the owned group must not outlive the cancel")
        outcome = adapter.collect(handle, context)
        self.assertEqual(outcome.status, "cancelled", outcome.error)
        self.assertTrue(outcome.shutdown_confirmed)


if __name__ == "__main__":
    unittest.main()
