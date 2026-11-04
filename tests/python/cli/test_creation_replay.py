"""ADR-032 creation replay through the real CLI, named transport and service."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import subprocess
from unittest import mock

from support import BoardTestCase, enable_fixture_buddy

from hey_my_buddy.blackboard.store.db import DB_FILE
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.cli import main as cli
from hey_my_buddy.protocol import transport


BUDDY = {"harness": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off"}
BUDDY_NAME = ":".join(BUDDY.values())


class CreationReplayTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.workdir("repo")
        self.git("init", "-q")
        self.git("config", "user.name", "Creation Replay Test")
        self.git("config", "user.email", "replay@example.invalid")
        (self.repo / "tracked.txt").write_text("original\n", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.git("commit", "-qm", "initial")
        (self.repo / "nested").mkdir()
        self.catalog_fixture()
        store = BoardStore(self.directory)
        try:
            store.initialize()
            enable_fixture_buddy(store, BUDDY)
        finally:
            store.db.close()

    def git(self, *args):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_GLOBAL=os.devnull)
        result = subprocess.run(["git", "-C", str(self.repo), *args], env=environment,
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def cli_call(self, method, packet, *, lose_reply=False):
        """Only the reply is lost after the named C-Two service commits."""
        observed = []
        real_request = transport._request
        expected_operation = {"create": "workflow_create", "submit": "workflow_submit"}[method]

        def request(endpoint, operation, wire, resource="control", *, state_dir):
            self.assert_rpc_state_dir(state_dir)
            result = real_request(endpoint, operation, wire, resource, state_dir=state_dir)
            if operation == expected_operation:
                observed.append((operation, dict(wire), result))
            if lose_reply and operation == expected_operation:
                raise OSError("simulated reply loss after service commit")
            return result

        output = io.StringIO()
        with mock.patch.object(transport, "_request", side_effect=request), contextlib.redirect_stdout(output):
            code = cli.main([method, json.dumps(packet)], credential_document=None)
        return code, json.loads(output.getvalue()), observed

    def facts(self):
        with sqlite3.connect(self.directory / DB_FILE) as db:
            return {
                "macro": db.execute("SELECT macro_task_id,project_id,creation_key FROM macro_tasks").fetchall(),
                "goals": db.execute("SELECT macro_task_id,revision,body FROM macro_task_goals").fetchall(),
                "runs": db.execute("SELECT run_id,creation_key,workspace_id,workspace_manifest_sha256,execution_workspace_json FROM workflow_runs").fetchall(),
                "tasks": db.execute("SELECT task_id,request_id FROM tasks").fetchall(),
                "reservations": db.execute("SELECT reservation_id,workspace_id,manifest_sha256 FROM workspace_reservations").fetchall(),
                "inputs": db.execute("SELECT artifact_id,workspace_id,input_manifest_sha256 FROM workflow_artifacts WHERE kind='input'").fetchall(),
            }

    def test_create_and_submit_replay_after_committed_replies_are_lost(self):
        private_environment = self.child_environment()
        with mock.patch.dict(os.environ, private_environment, clear=True), self.daemon():
            marker_before = workspace.read_project_id(self.repo)
            self.assertIsNone(marker_before)

            create = {"cwd": str(self.repo / "nested"), "title": "Review plan", "goal": "Original goal"}
            with mock.patch.object(cli, "get_state_dir", return_value=self.directory):
                code, lost, first = self.cli_call("create", create, lose_reply=True)
                self.assertEqual(code, 1)
                self.assertEqual(lost["error"]["code"], "SERVICE_ERROR")
                self.assertEqual(len(first), 1)
                self.assertEqual(first[0][0], "workflow_create")
                self.assertFalse(first[0][2]["alreadyExists"])
                marker = workspace.read_project_id(self.repo)
                self.assertIsNotNone(marker)
                after_create = self.facts()
                self.assertEqual(len(after_create["macro"]), 1)
                self.assertEqual(after_create["macro"][0][1], marker)

                code, replayed, second = self.cli_call("create", create)
                self.assertEqual(code, 0, replayed)
                self.assertEqual(replayed["macroTaskId"], first[0][2]["macroTaskId"])
                self.assertTrue(replayed["alreadyExists"])
                self.assertEqual(first[0][1]["creationKey"], second[0][1]["creationKey"])
                self.assertEqual(self.facts(), after_create)
                self.assertEqual(workspace.read_project_id(self.repo), marker)

                submit = {"cwd": str(self.repo), "macroTaskId": replayed["macroTaskId"],
                          "title": "Inspect implementation", "task": "Inspect the current implementation",
                          "buddy": BUDDY_NAME, "checkout": "worktree", "access": "read",
                          "inputs": [], "expectedSeconds": 600}
                code, lost, first = self.cli_call("submit", submit, lose_reply=True)
                self.assertEqual(code, 1, lost)
                self.assertEqual(lost["error"]["code"], "SERVICE_ERROR")
                self.assertEqual(len(first), 1)
                self.assertEqual(first[0][0], "workflow_submit")
                self.assertFalse(first[0][2]["alreadyExists"])
                after_submit = self.facts()
                self.assertEqual(len(after_submit["runs"]), 1)
                self.assertEqual(len(after_submit["tasks"]), 1)
                self.assertEqual(len(after_submit["reservations"]), 1)
                self.assertEqual(len(after_submit["inputs"]), 1)
                self.assertEqual(after_submit["runs"][0][0], first[0][2]["runId"])

                code, replayed, second = self.cli_call("submit", submit)
                self.assertEqual(code, 0, replayed)
                self.assertEqual(replayed["runId"], first[0][2]["runId"])
                self.assertTrue(replayed["alreadyExists"])
                self.assertEqual(replayed["access"], "read")
                self.assertEqual(replayed["checkout"], "worktree")
                self.assertEqual(replayed["buddy"], BUDDY)
                self.assertEqual(first[0][1]["creationKey"], second[0][1]["creationKey"])
                self.assertEqual(self.facts(), after_submit)
                self.assertEqual(workspace.read_project_id(self.repo), marker)

                changed_choices = {**submit, "buddy": "route", "checkout": "existing",
                                   "access": "write", "writeScope": ["."], "expectedSeconds": 1200}
                code, original_choice, third = self.cli_call("submit", changed_choices)
                self.assertEqual(code, 0, original_choice)
                self.assertEqual(third[0][1]["creationKey"], first[0][1]["creationKey"])
                self.assertEqual(original_choice["runId"], first[0][2]["runId"])
                self.assertTrue(original_choice["alreadyExists"])
                self.assertEqual(original_choice["access"], "read")
                self.assertEqual(original_choice["checkout"], "worktree")
                self.assertEqual(original_choice["buddy"], BUDDY)
                self.assertEqual(self.facts(), after_submit)
                self.assertEqual(len([line for line in self.git("worktree", "list", "--porcelain").splitlines()
                                      if line.startswith("worktree ")]), 2)

            # Service control.json is its own endpoint, not a Host control cache.
            self.assertFalse((self.directory / "controls").exists())
            self.assertFalse((self.directory / "submissions").exists())


if __name__ == "__main__":
    import unittest
    unittest.main()
