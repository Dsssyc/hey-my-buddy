"""Session-path tests over the fake agent: the full native session surface.

Covers session/new's required ``mcpServers`` key and the stdio ``env`` array
shape, configuration echo, list/resume/close, the cancel notification (a
transport fact, never a stop proof), a leader that exits while its group
survives, and a slow EOF drain.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest

from hey_my_buddy.buddy.harnesses.dsh.acp import AcpClient
from hey_my_buddy.buddy.harnesses.dsh.acp.connection import AcpConnectionClosed, AcpRequestError
from hey_my_buddy.paths import native_path
from hey_my_buddy.private_dirs import remove_tree

from .support import AcpTestCase, MCP_STUB


class NewSessionTest(AcpTestCase):
    def test_session_storage_failure_answers_and_logs_the_failed_request(self):
        blocked = self.test_dir / "blocked-sessions"
        blocked.write_text("a file cannot hold sessions", encoding="utf-8")
        patch = self.test_dir / "launch-patch.json"
        patch.write_text(json.dumps([
            {"id": "session-persistence-jsonl", "config": {"root": str(blocked)}}]),
            encoding="utf-8")
        client = self.start_client("--patch", str(patch))
        client.initialize(timeout=15.0)
        with self.assertRaises(AcpRequestError) as caught:
            client.new_session(str(self.root), timeout=15.0)
        self.assertEqual(-32603, caught.exception.error["code"])
        self.assertIn("FileExistsError", caught.exception.error["message"])
        failures = [entry for entry in self.agent_log() if entry.get("event") == "request-error"]
        self.assertEqual(1, len(failures))
        self.assertEqual("session/new", failures[0]["method"])
        self.assertIsInstance(failures[0]["id"], int)
        self.assertIn("persist_session", failures[0]["traceback"])
        self.assertEqual([], client.connection.request("session/list", {}, 15.0)["sessions"])

    def test_deep_patch_and_session_store_keep_the_pinned_paths(self):
        branch = self.test_dir / "deep-store"
        deep = branch
        while len(str(deep)) < 330:
            deep /= "session-directory-" + "x" * 30
        native_path(deep).mkdir(parents=True)
        self.addCleanup(remove_tree, branch)
        sessions = deep / "sessions"
        storage = deep / "storages"
        patch = deep / "launch-patch.json"
        native_path(patch).write_text(json.dumps([
            {"id": "session-persistence-jsonl", "config": {"root": str(sessions)}},
            {"id": "storage-json", "config": {"root": str(storage)}}]), encoding="utf-8")
        client = self.start_client("--patch", str(patch), "--prompt-mode", "final",
                                   "--session-record", "plain")
        client.initialize(timeout=15.0)
        session_id = client.new_session(str(self.root), timeout=15.0)["sessionId"]
        client.set_config_option(session_id, "reasoning_effort", "low", timeout=15.0)
        client.resume_session(session_id, str(self.root), timeout=15.0)
        self.assertEqual("end_turn", client.prompt(session_id, "private fixture", timeout=30.0)["stopReason"])
        marker = sessions / f"{session_id}.fake-session.json"
        self.assertEqual("low", json.loads(native_path(marker).read_text(encoding="utf-8"))["effort"])
        self.assertTrue(native_path(sessions / f"{session_id}.v3.jsonl").is_file())
        index = storage / "session_projcache" / "sessions" / f"{session_id}.json"
        self.assertEqual(str(self.root), json.loads(native_path(index).read_text(encoding="utf-8"))["cwd"])
        client.close_session(session_id, timeout=15.0)
        resumed_root = self.sibling_dir("resumed-native-root")
        (resumed_root / "logs").mkdir()
        resumed = AcpClient.start(self.agent_argv("--patch", str(patch)), private_root=resumed_root)
        self.addCleanup(self._shutdown_client, resumed)
        resumed.initialize(timeout=15.0)
        configuration = resumed.resume_session(session_id, str(self.root), timeout=15.0)["configOptions"]
        self.assertEqual("low", configuration[1]["currentValue"])
        resumed.close_session(session_id, timeout=15.0)

    def test_new_session_always_carries_mcp_servers(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        session = client.new_session(str(self.root), timeout=15.0)
        self.assertEqual("fake-session-1", session["sessionId"])
        self.assertIsNone(session["modes"])
        self.assertEqual("model", session["configOptions"][0]["id"])
        client.close_session(session["sessionId"], timeout=15.0)

    def test_declared_mcp_server_is_mounted_over_the_wire(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        servers = [{"name": "hey-my-buddy-test", "command": sys.executable, "args": [str(MCP_STUB)],
                    "env": []}]
        session = client.new_session(str(self.root), mcp_servers=servers, timeout=15.0)
        mounted = session["mountedMcp"][0]
        self.assertTrue(mounted["mounted"])
        self.assertEqual(["deliver_outcome"], mounted["tools"])
        client.close_session(session["sessionId"], timeout=15.0)
        evidence = client.shutdown(drain_seconds=10.0, settle_seconds=2.0)
        self.assertTrue(evidence["shutdownConfirmed"])
        mounted_events = [entry for entry in self.agent_log() if entry.get("event") == "stub-mounted"]
        self.assertEqual(1, len(mounted_events))

    def test_mcp_mount_allows_slow_child_startup(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        servers = [{"name": "slow-private-mcp", "command": sys.executable,
                    "args": ["-c", "import runpy,sys,time; time.sleep(6); runpy.run_path(sys.argv[1], run_name='__main__')",
                             str(MCP_STUB)], "env": []}]
        session = client.new_session(str(self.root), mcp_servers=servers, timeout=30.0)
        self.assertTrue(session["mountedMcp"][0]["mounted"])
        self.assertEqual(["deliver_outcome"], session["mountedMcp"][0]["tools"])
        client.close_session(session["sessionId"], timeout=15.0)

    def test_mcp_child_exit_finishes_the_mount_without_waiting_for_the_deadline(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        servers = [{"name": "exited-private-mcp", "command": sys.executable,
                    "args": ["-c", "raise SystemExit(7)"], "env": []}]
        started = time.monotonic()
        session = client.new_session(str(self.root), mcp_servers=servers, timeout=30.0)
        self.assertFalse(session["mountedMcp"][0]["mounted"])
        self.assertIn("error", session["mountedMcp"][0])
        self.assertLess(time.monotonic() - started, 20.0)
        client.close_session(session["sessionId"], timeout=15.0)


class ConfigurationEchoTest(AcpTestCase):
    def test_set_config_option_echoes_the_whole_group(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        session = client.new_session(str(self.root), timeout=15.0)
        default_model = session["configOptions"][0]["currentValue"]
        self.assertEqual('["fake","m1"]', default_model)
        echoed = client.set_config_option(session["sessionId"], "model",
                                          '["fake","m2"]', timeout=15.0)
        self.assertEqual('["fake","m2"]', echoed["configOptions"][0]["currentValue"])
        self.assertEqual("high", echoed["configOptions"][1]["currentValue"])
        effort = client.set_config_option(session["sessionId"], "reasoning_effort",
                                          "low", timeout=15.0)
        self.assertEqual("low", effort["configOptions"][1]["currentValue"])
        self.assertEqual('["fake","m2"]', effort["configOptions"][0]["currentValue"])
        client.close_session(session["sessionId"], timeout=15.0)

    def test_unknown_session_is_a_request_error(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        with self.assertRaises(AcpRequestError) as caught:
            client.set_config_option("no-such-session", "model", "x", timeout=15.0)
        self.assertEqual(-32602, caught.exception.error["code"])


class ListResumeCloseTest(AcpTestCase):
    def test_list_resume_and_close(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        first = client.new_session(str(self.root), timeout=15.0)
        second = client.new_session(str(self.root / "work"), timeout=15.0)
        client.set_config_option(first["sessionId"], "reasoning_effort", "off", timeout=15.0)
        listed = client.connection.request("session/list", {}, 15.0)
        self.assertEqual({first["sessionId"], second["sessionId"]},
                         {entry["sessionId"] for entry in listed["sessions"]})
        resumed = client.resume_session(first["sessionId"], str(self.root), timeout=15.0)
        self.assertNotIn("sessionId", resumed, "the resume response carries no session id")
        self.assertEqual("off", resumed["configOptions"][1]["currentValue"])
        with self.assertRaises(AcpRequestError):
            client.resume_session("no-such-session", str(self.root), timeout=15.0)
        client.close_session(first["sessionId"], timeout=15.0)
        client.close_session(second["sessionId"], timeout=15.0)
        listed = client.connection.request("session/list", {}, 15.0)
        self.assertEqual([], listed["sessions"])


class CancelTest(AcpTestCase):
    def test_cancel_is_a_notification_and_never_a_stop_proof(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        session = client.new_session(str(self.root), timeout=15.0)
        prompt_result: list = []

        def run_prompt():
            prompt_result.append(client.prompt(session["sessionId"], "work", timeout=30.0))

        thread = threading.Thread(target=run_prompt)
        thread.start()
        self.wait_for(lambda: any(entry.get("event") == "permission-answer-1"
                                  for entry in self.agent_log()))
        client.cancel(session["sessionId"])
        thread.join(timeout=30)
        self.assertEqual("end_turn", prompt_result[0]["stopReason"])
        self.assertTrue(self.wait_for(
            lambda: any(entry.get("event") == "cancel-received" for entry in self.agent_log())),
            "the agent must record the cancel notification")
        # The cancel notification is transport only: the agent still runs.
        self.assertTrue(client.handle.group_alive())
        self.assertFalse(client.handle.shutdown_confirmed())
        client.close_session(session["sessionId"], timeout=15.0)


class StopStateTest(AcpTestCase):
    def test_leader_exits_while_its_group_survives(self):
        client = self.start_client("--orphan-on", "session/list")
        client.initialize(timeout=15.0)
        with self.assertRaises(AcpConnectionClosed):
            client.connection.request("session/list", {}, 15.0)
        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import stop_evidence
        before = stop_evidence(client.handle)
        self.assertEqual("alive", before["groupObserved"])
        self.assertFalse(before["shutdownConfirmed"])
        started = time.monotonic()
        evidence = client.shutdown(drain_seconds=10.0, settle_seconds=2.0)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0, "a reaped root must not consume the 12 second EOF grace")
        self.assertTrue(evidence["leaderExited"])
        self.assertEqual(0, evidence["leaderExitCode"])
        self.assertEqual("gone", evidence["groupObserved"],
                         "shutdown must stop surviving members of its owned group")
        self.assertTrue(evidence["shutdownConfirmed"])

    def test_slow_eof_drain_waits_for_the_leader(self):
        client = self.start_client("--survive-eof", "1.0")
        client.initialize(timeout=15.0)
        started = time.monotonic()
        evidence = client.shutdown(drain_seconds=10.0, settle_seconds=2.0)
        self.assertGreaterEqual(time.monotonic() - started, 0.9)
        self.assertTrue(client.connection.eof.is_set())
        self.assertTrue(evidence["leaderExited"])
        self.assertTrue(evidence["shutdownConfirmed"])

    def test_eof_drain_budget_is_kept_before_forcing_native_cleanup(self):
        client = self.start_client("--survive-eof", "0.4")
        client.initialize(timeout=15.0)
        evidence = client.shutdown(drain_seconds=1.0, settle_seconds=0.05)
        self.assertTrue(evidence["shutdownConfirmed"])
        self.assertEqual(0, evidence["leaderExitCode"],
                         "native cleanup within the existing drain budget must finish before force")

    def test_shutdown_without_any_session_still_reports_facts(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        evidence = client.shutdown(drain_seconds=10.0, settle_seconds=2.0)
        self.assertTrue(evidence["shutdownConfirmed"])
        facts = client.facts()
        self.assertEqual(0, facts["protocolFaultCount"])
        self.assertEqual(0, facts["unclaimedReplyCount"])
        self.assertTrue(facts["eof"])

    def test_unconfirmed_stop_fails_the_test_with_the_observed_evidence(self):
        """The shutdown guard presents an unconfirmed stop as a test failure.

        The synthetic observation is fully in-memory: a Job-like handle whose
        group is still active drives the production ``group_observation`` job
        branch to ``alive`` with ``shutdownConfirmed`` false on every platform,
        and no process group of this run is touched. The guard must raise with
        that evidence in the message - the failure is the record, and no
        directory or ledger is preserved.
        """

        class UnstoppableHandle:
            process = type("FakeProcess", (), {"poll": lambda self: None})()
            job = type("ActiveJob", (), {"active": lambda self: 1})()

            def terminate(self, *, grace_seconds):
                return None

            def wait(self, timeout):
                return None

            def group_alive(self):
                return True

        class UnconfirmedClient:
            handle = UnstoppableHandle()

            def shutdown(self, *, drain_seconds, settle_seconds):
                return {"shutdownConfirmed": False, "leaderExited": False}

        with self.assertRaises(AssertionError) as caught:
            self._shutdown_client(UnconfirmedClient())
        message = str(caught.exception)
        self.assertIn("did not stop", message)
        self.assertIn("alive", message, "the observed evidence must be in the failure")


if __name__ == "__main__":  # pragma: no cover - direct execution convenience
    unittest.main()
