"""Shared harness for the ACP client tests: one registered container per run.

Every test creates its materials inside a dedicated container under the
checkout's ignored ``tmp/`` (never the system temp tree). Each created path is
registered - exact path, purpose, creation time - in the run's ``manifest.jsonl``
the moment it is created; each successful deletion is appended to
``deletions.jsonl``. A test whose child group does not confirm stop preserves
its directory, fails the test with the reason, and deletes nothing. The run
container and both ledgers are always kept; only registered per-test
directories are removed, and removal errors fail the test instead of being
masked. Siblings that must sit outside a test's native private root (symlink
targets, out-of-root directories) live inside the same container, as siblings
of the native root.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"
MCP_STUB = Path(__file__).resolve().parent / "mcp_stub.py"
CHECKOUT = Path(__file__).resolve().parents[6]
TEST_NAMESPACE = CHECKOUT / "tmp" / "adr025-dsh-acp-tests"

_RUN: dict = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _append(path: Path, entry: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def run_container() -> Path:
    """One mkdtemp container per test process; registered on creation, kept."""
    if "path" not in _RUN:
        TEST_NAMESPACE.mkdir(mode=0o700, parents=True, exist_ok=True)
        container = Path(tempfile.mkdtemp(prefix="run-", dir=TEST_NAMESPACE)).resolve()
        container.chmod(0o700)
        _append(container / "manifest.jsonl",
                {"path": str(container), "purpose": "run container", "createdAt": _now()})
        _RUN["path"] = container
    return _RUN["path"]


class AcpTestCase(unittest.TestCase):
    """One registered per-test directory holding the native private root."""

    def setUp(self) -> None:
        self.container = run_container()
        self.test_dir = self.container / f"t-{self.id().rsplit('.', 1)[-1]}-{uuid.uuid4().hex[:8]}"
        self.test_dir.mkdir(mode=0o700)
        self._register(self.test_dir, "per-test directory")
        self._deletable: list[Path] = [self.test_dir]
        self.preserved: list[str] = []
        self.root = self.test_dir / "native-root"
        self.dsh_home = self.root / "dsh-home"
        self.home = self.root / "home"
        self.logs = self.root / "logs"
        self.dsh_home.mkdir(mode=0o700, parents=True)
        self.home.mkdir(mode=0o700)
        self.logs.mkdir(mode=0o700)
        self._register(self.root, "native private root")
        self.addCleanup(self._remove_created)

    def _register(self, path: Path, purpose: str) -> None:
        self._append_manifest({"path": str(path), "purpose": purpose, "createdAt": _now()})

    def _append_manifest(self, entry: dict) -> None:
        _append(self.container / "manifest.jsonl", entry)

    def _append_deletion(self, path: Path) -> None:
        _append(self.container / "deletions.jsonl",
                {"path": str(path), "deletedAt": _now(), "test": self.id()})

    def sibling_dir(self, name: str) -> Path:
        """A registered directory inside this test's dir but outside the native
        private root - for symlink targets and out-of-root rejection cases."""
        path = self.test_dir / name
        path.mkdir(mode=0o700)
        self._register(path, f"sibling outside the native root: {name}")
        return path

    def preserve(self, reason: str) -> None:
        """Keep this test's directory and make the coming cleanup fail loudly."""
        self.preserved.append(reason)

    def _remove_created(self) -> None:
        if self.preserved:
            raise AssertionError(
                "test directories preserved because a stop was not confirmed: "
                + "; ".join(self.preserved))
        failures = []
        for path in reversed(self._deletable):
            try:
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.exists():
                    shutil.rmtree(path)
                self._append_deletion(path)
            except OSError as error:
                failures.append(f"{path}: {error}")
        if failures:
            raise AssertionError("registered deletions failed: " + "; ".join(failures))

    def agent_argv(self, *extra: str) -> list[str]:
        """The fake agent launched like the native one: absolute executable, log path."""
        return [sys.executable, str(FAKE_AGENT), "--log", str(self.logs / "fake-agent.log"), *extra]

    def start_client(self, *extra: str, **kwargs):
        """Start the client against the fake agent with this test's private root."""
        from hey_my_buddy.buddy.harnesses.dsh.acp import AcpClient
        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import FrameMetaLog

        if "frame_log" not in kwargs:
            kwargs["frame_log"] = FrameMetaLog(self.logs / "frames.jsonl")
        client = AcpClient.start(self.agent_argv(*extra), private_root=self.root,
                                 dsh_home=self.dsh_home, home=self.home, **kwargs)
        self.addCleanup(self._shutdown_client, client)
        return client

    def _shutdown_client(self, client) -> None:
        """Shut the client down; never delete state behind an unconfirmed stop.

        Every stop or observation failure - the shutdown itself, the terminate,
        the final wait, the last observation - preserves this test's directory
        with its reason and fails the test. Nothing is deleted while a stop is
        unconfirmed.
        """
        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import stop_evidence

        try:
            evidence = client.shutdown(drain_seconds=10.0, settle_seconds=2.0)
        except BaseException as error:  # noqa: BLE001 - preserved, reported, nothing deleted
            self.preserve(f"shutdown raised: {error!r}")
            raise
        if evidence.get("shutdownConfirmed"):
            return
        try:
            client.handle.terminate(grace_seconds=3.0)
            client.handle.wait(10)
            final = stop_evidence(client.handle)
        except BaseException as error:  # noqa: BLE001 - preserved, reported, nothing deleted
            self.preserve(f"stop confirmation raised: {error!r}")
            raise AssertionError(
                f"stop confirmation failed; {self.test_dir} preserved") from error
        if not final.get("shutdownConfirmed"):
            self.preserve(f"group not confirmed gone: {final}")
            raise AssertionError(
                f"test child group did not stop; {self.test_dir} preserved")

    def agent_log(self) -> list[dict]:
        path = self.logs / "fake-agent.log"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def wait_for(self, predicate, timeout: float = 10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            value = predicate()
            if value:
                return value
            time.sleep(0.02)
        return None
