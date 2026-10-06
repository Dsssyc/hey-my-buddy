"""DSH native session storage: the session rollout stays execution-private.

The ACP run pins the session-record root into the attempt's private ``DSH_HOME``
through the same supported per-run patch overlay the retired runner used, so the
DSH home, credentials store and settings document keep resolving in the owning
harness and native auth is never relocated (:func:`.native_run.session_facts`
reports these storage facts; the role projections are pinned in the role-wiring
tests). ADR-021 decision 18 removed the DSH workspace grouping feature, so no
run accepts a workspace switch.

The first class pins the submission rule of that removal in the public spec
schema. The second class drives the real ``harnesses/dsh/scripts/run.mjs`` with
a stub ``dsh`` launcher — the Node integration stays until ADR-025 step 4-C
deletes it, and these are its remaining witnesses. The third class covers the
normalized no-deadline sentinel (``timeoutSeconds=0``) on the registered run:
it is an unbounded deadline, it survives a real delay, and it stays cancellable
through its owned group; the retired Node carriers' equivalent witnesses are
registered in the wiring disposition table.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

from hey_my_buddy.buddy.harnesses.dsh.native_run import execution_deadline, run
from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
from hey_my_buddy.protocol.schemas import normalize_spec

from buddy.harnesses.dsh.test_native_run import NativeRunCase

ROOT = Path(__file__).resolve().parents[5]
RUNNER = ROOT / "harnesses/dsh/scripts/run.mjs"
TASK = "Do the bounded thing.\n"
# Inherited Buddy runtime/worker/credential variables must never leak into a
# test subprocess, and the child must never see a pinned dev environment.
CLEARED_ENV = (
    "BUDDY_STATE_DIR", "BUDDY_RUNTIME_ROOT", "BUDDY_RUNTIME", "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE", "BUDDY_WORKER_ID", "BUDDY_AGENT_CREDENTIAL",
    "BUDDY_AGENT_CREDENTIAL_FILE", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT",
    "DSH_SETTINGS_FILE", "DSH_BIN", "DSH_DELEGATE_MODEL", "DSH_DELEGATE_PROVIDER",
    "DSH_DELEGATE_EFFORT",
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
    def test_the_removed_workspace_switch_is_rejected_on_submission(self):
        from hey_my_buddy.errors import BoardError
        with tempfile.TemporaryDirectory(prefix="buddy-dsh-spec-") as root:
            common = {"requestId": "dsh-default", "task": "bounded task", "cwd": root}
            # Every DSH run is execution-private now: the grouping switch is an
            # unknown submit field whatever adapter or value carries it.
            for params in ({**common, "workspace": True}, {**common, "workspace": False},
                           {**common, "adapter": "codex", "workspace": False}):
                with self.subTest(params=params):
                    with self.assertRaises(BoardError) as raised:
                        normalize_spec(params)
                    self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
                    self.assertEqual(raised.exception.details.get("field"), "workspace")
            spec = normalize_spec({**common, "adapter": "dsh"})
            self.assertNotIn("workspace", spec)


@unittest.skipUnless(shutil.which("node") and RUNNER.is_file(), "Node.js and the dsh runner are required")
class DshSessionRootTests(unittest.TestCase):
    """The retired Node runner's private-session-root witnesses (until 4-C).

    These cases exist only for the ``harnesses/dsh/`` Node tree that step 4-C
    deletes together with them; the registered run's equivalent facts are the
    launch-patch pins in the native-run tests and :func:`session_facts`.
    """

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

    def invoke(self, *flags: str) -> subprocess.CompletedProcess:
        base = [
            shutil.which("node"), str(RUNNER),
            "--cwd", str(self.checkout),
            "--task-file", str(self.task),
            "--dsh-bin", str(self.stub),
            "--log-dir", str(self.root / "logs"),
        ]
        return subprocess.run([*base, *flags], capture_output=True, text=True, timeout=60,
                              env=self.environment())

    def test_an_explicit_session_root_moves_only_the_session_root_and_keeps_the_owning_home(self):
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

    def test_default_run_uses_a_private_session_root(self):
        completed = self.invoke()
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertNotIn("workspace", result)
        self.assertEqual(result["nativeStorage"]["scope"], "run-private-sessions")
        self.assertTrue(result["nativeStorage"]["sessionRootPrivate"])
        log_dir = Path(result["logPaths"]["stdout"]).parent
        self.assertTrue((log_dir / "sessions").is_dir())
        rows = json.loads((self.artifacts / "patch.json").read_text())
        session_row = next(row for row in rows if row.get("id") == "session-persistence-jsonl")
        self.assertEqual(session_row["config"], {"root": str(log_dir / "sessions")})

    def test_the_removed_grouping_flags_are_usage_errors(self):
        # The product grouping feature is gone: its runner flags are rejected
        # before any spawn, whatever they would have combined with.
        for flags in (["--workspace"], ["--no-workspace"],
                      ["--workspace", "--session-root=" + str(self.session_root)],
                      ["--attach-session", "session-1"],
                      ["--workspace-socket", "/tmp/absent.sock"]):
            with self.subTest(flags=flags):
                completed = self.invoke(*flags)
                self.assertEqual(completed.returncode, 2, completed.stdout)
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
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("--dsh-home", completed.stderr)
        self.assertFalse((self.artifacts / "argv.txt").exists())


class NoDeadlineSentinelTests(NativeRunCase):
    """``timeoutSeconds=0`` is a real unbounded deadline on the registered run.

    The retired carriers proved the sentinel end to end through the Node runner;
    the registered run proves it at its own deadline seam: the normalized budget
    becomes an infinite deadline, a real delay survives it, and the bounded
    waits inside the ACP connection never turn the infinite budget into an
    expiry.
    """

    def test_the_normalized_zero_budget_is_an_infinite_deadline(self):
        self.assertEqual(execution_deadline(0), math.inf)
        self.assertGreater(execution_deadline(3), time.monotonic())

    def test_a_zero_timeout_attempt_survives_a_real_delay_and_completes(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--delay", "session/prompt:2",
                                 "--final-answer", '{"choice":"a"}']
        result = run(self.fast_request("prompt", timeout=0),
                     observer=lambda _facts: FEEDBACK_CONTINUE, services=None,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_a_zero_timeout_attempt_stays_cancellable_through_its_owned_group(self):
        self.extra_agent_args = ["--prompt-mode", "final", "--hang-prompt"]
        flag = threading.Event()
        result_holder: list = []

        def stop_soon():
            self.assertTrue(self.wait_for(lambda: any(
                entry.get("event") == "hanging-prompt" for entry in self.agent_log())))
            flag.set()

        thread = threading.Thread(target=stop_soon)
        thread.start()
        try:
            result_holder.append(run(self.fast_request("prompt", timeout=0, scope="write"),
                                     observer=lambda _f: FEEDBACK_CONTINUE,
                                     services=None, cancelled=flag.is_set))
        finally:
            thread.join()
        result = result_holder[0]
        self.assertEqual(result.end.status, "cancelled")
        self.assertTrue(result.stop_evidence.interrupt.requested)
        self.assertEqual(result.stop_evidence.native.group_state, "gone",
                         "the cancelled unbounded attempt's group is confirmed gone")


if __name__ == "__main__":
    unittest.main()
