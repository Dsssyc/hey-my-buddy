"""Native groups survive their role controller, so recovery needs each identity."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.buddy.harnesses import discovery
from hey_my_buddy.buddy.runtime.windows_process import owned_popen
from hey_my_buddy.protocol import process_identity as identity


BINDING = {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1}


class NativeProcessJournalTests(unittest.TestCase):
    def test_journal_path_preserves_large_creation_token_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            token = 13_425_896_000_000_001
            path = identity.native_process_journal_path(
                Path(directory), {"pid": 73, "startedAt": token})
            self.assertEqual(path.name, f"native-process-journal-73-{token}.json")

    def test_every_owned_native_group_is_durable_before_handle_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = identity.native_process_journal_path(Path(directory))
            journal = identity.NativeProcessJournal(path, BINDING)
            handles = []
            try:
                with mock.patch.object(identity, "_active_native_journal", journal):
                    for _ in range(2):
                        process = owned_popen(["/bin/sleep", "30"], start_new_session=True)
                        handles.append(ProcessHandle(process, own_group=True, log_paths={}))
                evidence = identity.read_native_process_journal(path, BINDING)
                self.assertIsNotNone(evidence)
                self.assertEqual(evidence["pending"], 0)
                self.assertEqual({entry["pid"] for entry in evidence["identities"]},
                                 {handle.pid for handle in handles})
                for entry in evidence["identities"]:
                    self.assertEqual(entry["worker"]["pid"], os.getpid())
                    self.assertEqual(identity.inspect_process_identity(entry), "alive")
            finally:
                for handle in handles:
                    handle.terminate(grace_seconds=0)
                    handle.process.wait(timeout=5)

    def test_unresolved_spawn_intent_blocks_a_complete_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = identity.native_process_journal_path(Path(directory))
            journal = identity.NativeProcessJournal(path, BINDING)
            journal.begin()
            self.assertEqual(identity.read_native_process_journal(path, BINDING),
                             {"identities": [], "pending": 1})
            self.assertIsNone(identity.read_native_process_journal(path, {**BINDING, "generation": 2}))
            self.assertIsNone(identity.read_native_process_journal(path.parent / "missing", BINDING))

    def test_spawn_failure_with_no_child_clears_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = identity.native_process_journal_path(Path(directory))
            journal = identity.NativeProcessJournal(path, BINDING)
            with mock.patch.object(identity, "_active_native_journal", journal):
                with self.assertRaises(FileNotFoundError):
                    owned_popen(["/nonexistent/hey-my-buddy-test-command"], start_new_session=True)
            self.assertEqual(identity.read_native_process_journal(path, BINDING),
                             {"identities": [], "pending": 0})

    def test_version_probe_in_controller_records_its_separate_group(self):
        with tempfile.TemporaryDirectory() as directory:
            path = identity.native_process_journal_path(Path(directory))
            journal = identity.NativeProcessJournal(path, BINDING)
            with mock.patch.object(identity, "_active_native_journal", journal):
                reason, output = discovery.probe(
                    [sys.executable, "-c", "import time; time.sleep(0.1); print('version')"],
                    dict(os.environ))
            self.assertIsNone(reason)
            self.assertEqual(output.strip(), b"version")
            evidence = identity.read_native_process_journal(path, BINDING)
            self.assertEqual(evidence["pending"], 0)
            self.assertEqual(len(evidence["identities"]), 1)


if __name__ == "__main__":
    unittest.main()
