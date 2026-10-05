"""Shared harness for the ACP client tests: one private directory per test.

Every test creates its materials inside its own ``TemporaryDirectory`` under
the check runner's private temp root - ``BUDDY_CHECKS_TMPDIR``, which the check
suite provides and a direct focused run must set itself, like the other
suites. The fixture is removed during normal teardown once the child group's
stop is confirmed; nothing is registered, no run container or
manifest/deletion ledger is kept anywhere, and objects from earlier runs or
other sessions are never scanned or touched. Siblings that must sit outside a
test's native private root (symlink targets, out-of-root directories) live
inside the same per-test directory, as siblings of the native root. A child
whose stop stays unconfirmed fails the test with the observed evidence - the
failure itself is the record, so the tree stays free of persistent ledgers.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"
MCP_STUB = Path(__file__).resolve().parent / "mcp_stub.py"


class AcpTestCase(unittest.TestCase):
    """One private per-test directory holding the native private root."""

    def setUp(self) -> None:
        name = self.id().rsplit(".", 1)[-1]
        self.container = tempfile.TemporaryDirectory(
            prefix=f"acp-{name}-", dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.container.cleanup)
        self.test_dir = Path(self.container.name).resolve()
        self.root = self.test_dir / "native-root"
        self.dsh_home = self.root / "dsh-home"
        self.home = self.root / "home"
        self.logs = self.root / "logs"
        self.dsh_home.mkdir(mode=0o700, parents=True)
        self.home.mkdir(mode=0o700)
        self.logs.mkdir(mode=0o700)

    def sibling_dir(self, name: str) -> Path:
        """A directory inside this test's dir but outside the native private
        root - for symlink targets and out-of-root rejection cases."""
        path = self.test_dir / name
        path.mkdir(mode=0o700)
        return path

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
        """Shut the client down and require the owned group's stop to be confirmed.

        EOF, a session close or a cancel acknowledgement never prove the group
        stopped, so the evidence is checked here. A raised shutdown or an
        unconfirmed stop terminates the owned group once more - ownership is
        kept while failing - and then fails the test with what was observed.
        Nothing is preserved and no ledger is written anywhere.
        """
        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import stop_evidence

        try:
            evidence = client.shutdown(drain_seconds=10.0, settle_seconds=2.0)
        except BaseException:
            _terminate_quietly(client.handle)
            raise
        if evidence.get("shutdownConfirmed"):
            return
        _terminate_quietly(client.handle)
        final = stop_evidence(client.handle)
        if not final.get("shutdownConfirmed"):
            raise AssertionError(f"test child group did not stop; observed: {final}")

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


def _terminate_quietly(handle) -> None:
    """Keep ownership while a test is already failing; never mask that failure."""
    try:
        handle.terminate(grace_seconds=3.0)
        handle.wait(10)
    except Exception:  # noqa: BLE001 - the original failure must surface
        pass
