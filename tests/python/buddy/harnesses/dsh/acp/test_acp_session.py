"""Session-path tests over the fake agent: the full native session surface.

Covers session/new's required ``mcpServers`` key and the stdio ``env`` array
shape, configuration echo, list/resume/close, the cancel notification (a
transport fact, never a stop proof), a leader that exits while its group
survives, and a slow EOF drain.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest

from hey_my_buddy.buddy.harnesses.dsh.acp import AcpClient
from hey_my_buddy.buddy.harnesses.dsh.acp.client import PermissionPolicy
from hey_my_buddy.buddy.harnesses.dsh.acp.connection import AcpConnectionClosed, AcpRequestError
from hey_my_buddy.errors import BoardError

from .support import AcpTestCase, MCP_STUB


class NewSessionTest(AcpTestCase):
    def test_new_session_always_carries_mcp_servers(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        session = client.new_session(str(self.root), timeout=15.0)
        self.assertEqual("fake-session-1", session["sessionId"])
        self.assertIsNone(session["modes"])
        self.assertEqual("model", session["configOptions"][0]["id"])
        client.close_session(session["sessionId"], timeout=15.0)

    def test_stdio_env_must_be_an_array_of_name_value_objects(self):
        with self.assertRaises(BoardError):
            AcpClient._checked_mcp_servers(
                [{"name": "broken", "command": "/bin/true", "env": {"A": "B"}}])
        with self.assertRaises(BoardError):
            AcpClient._checked_mcp_servers(
                [{"name": "broken", "command": "/bin/true", "env": ["A=B"]}])
        servers = AcpClient._checked_mcp_servers(
            [{"name": "ok", "command": "/bin/true", "args": [], "env": []}])
        self.assertEqual([], servers[0]["env"])
        self.assertEqual([], AcpClient._checked_mcp_servers(None))

    def test_declared_mcp_server_is_mounted_over_the_wire(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        servers = [{"name": "buddy-test", "command": sys.executable, "args": [str(MCP_STUB)],
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
        listed = client.list_sessions(timeout=15.0)
        self.assertEqual({first["sessionId"], second["sessionId"]},
                         {entry["sessionId"] for entry in listed["sessions"]})
        resumed = client.resume_session(first["sessionId"], str(self.root), timeout=15.0)
        self.assertNotIn("sessionId", resumed, "the resume response carries no session id")
        self.assertEqual("off", resumed["configOptions"][1]["currentValue"])
        with self.assertRaises(AcpRequestError):
            client.resume_session("no-such-session", str(self.root), timeout=15.0)
        client.close_session(first["sessionId"], timeout=15.0)
        client.close_session(second["sessionId"], timeout=15.0)
        listed = client.list_sessions(timeout=15.0)
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


class ExactTitleTest(AcpTestCase):
    def test_wire_allow_requires_the_exact_listed_title(self):
        client = self.start_client("--allowed-title", "listed-title-extra",
                                   permission_policy=PermissionPolicy(allowed_titles=("listed-title",)))
        client.initialize(timeout=15.0)
        session = client.new_session(str(self.root), timeout=15.0)
        stop = client.prompt(session["sessionId"], "work", timeout=30.0)
        self.assertEqual("end_turn", stop["stopReason"])
        decisions = client.facts()["permissionDecisions"]
        self.assertEqual("cancelled", decisions[1]["outcome"]["outcome"],
                         "a title that merely starts with a listed one is not listed")
        self.assertIn("not on the explicit allow list", decisions[1]["basis"])

    def test_cleanup_guard_preserves_instead_of_deleting(self):
        import uuid

        from .support import AcpTestCase as _Case
        from .support import run_container

        probe = _Case("run")
        container = run_container()
        marker = container / f"guard-probe-{uuid.uuid4().hex[:8]}"
        marker.mkdir(mode=0o700)
        probe.container = container
        probe.id = lambda: "guard-probe"
        probe._append_manifest = lambda entry: None
        probe._append_deletion = lambda path: None
        probe._deletable = [marker]
        probe.preserved = ["synthetic unconfirmed stop"]
        with self.assertRaises(AssertionError):
            probe._remove_created()
        self.assertTrue(marker.is_dir(), "a preserved directory must not be deleted")
        probe.preserved = []
        probe._remove_created()
        self.assertFalse(marker.exists(), "a confirmed cleanup deletes the registered path")


class StopStateTest(AcpTestCase):
    def test_leader_exits_while_its_group_survives(self):
        client = self.start_client("--orphan-on", "session/list")
        client.initialize(timeout=15.0)
        with self.assertRaises(AcpConnectionClosed):
            client.list_sessions(timeout=15.0)
        evidence = client.shutdown(drain_seconds=10.0, settle_seconds=2.0)
        self.assertTrue(evidence["leaderExited"])
        self.assertEqual(0, evidence["leaderExitCode"])
        self.assertEqual("alive", evidence["groupObserved"],
                         "a surviving group member keeps the group alive")
        self.assertTrue(evidence["groupAliveConservative"])
        self.assertFalse(evidence["shutdownConfirmed"])
        client.handle.terminate(grace_seconds=3.0)
        self.wait_for(lambda: not client.handle.group_alive())
        final = client.shutdown(drain_seconds=1.0, settle_seconds=2.0)
        self.assertEqual("gone", final["groupObserved"])
        self.assertTrue(final["shutdownConfirmed"])

    def test_slow_eof_drain_waits_for_the_leader(self):
        client = self.start_client("--survive-eof", "1.0")
        client.initialize(timeout=15.0)
        started = time.monotonic()
        evidence = client.shutdown(drain_seconds=10.0, settle_seconds=2.0)
        self.assertGreaterEqual(time.monotonic() - started, 0.9)
        self.assertTrue(client.connection.eof.is_set())
        self.assertTrue(evidence["leaderExited"])
        self.assertTrue(evidence["shutdownConfirmed"])

    def test_shutdown_without_any_session_still_reports_facts(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        evidence = client.shutdown(drain_seconds=10.0, settle_seconds=2.0)
        self.assertTrue(evidence["shutdownConfirmed"])
        facts = client.facts()
        self.assertEqual(0, facts["protocolFaultCount"])
        self.assertEqual(0, facts["unmatchedResponseCount"])
        self.assertTrue(facts["eof"])


if __name__ == "__main__":  # pragma: no cover - direct execution convenience
    unittest.main()
