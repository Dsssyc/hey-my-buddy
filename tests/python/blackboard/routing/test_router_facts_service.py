"""Real private daemon and execution process; the native CLI is an offline peer."""
import json
import subprocess
from pathlib import Path

from support import BoardTestCase, wait_for
from buddy.harnesses.fixture_selection import select_fixture
from hey_my_buddy.protocol.client import BoardClient


class RouterFactsServiceTests(BoardTestCase):
    def test_private_service_routes_with_frozen_program_facts(self):
        root = Path(__file__).resolve().parents[4]
        native = root / "tests/python/buddy/harnesses/codex/fixtures/no_tool_codex.py"
        self.catalog_fixture({
            "source": "file:router-facts", "harnessVersion": "fixture", "discoveredAt": "2026-01-01T00:00:00Z",
            "providers": [{"provider": "openai", "harness": "codex", "efforts": ["low", "high"],
                           "models": [{"id": "fixture-model", "name": "Offline peer", "inputModalities": ["text"]}]}],
            "warnings": [],
        })
        ids = [f"codex:openai:fixture-model:{effort}" for effort in ("low", "high")]
        board = self.board()
        board.call("model_catalog_refresh", {"requestId": "private-catalog"})
        board.console_call("user_policy_publish", {
            "commandId": "private-settings", "buddySettings": [{"buddyId": buddy, "enabled": True} for buddy in ids],
            "configuration": {"defaultRoutingMode": "fast", "routerBuddyIds": [ids[0]]},
        })
        board.close()
        work = self.workdir("facts")
        (work / "README.md").write_text("Private routing fixture\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        subprocess.run(["git", "-C", str(work), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(work), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Fixture"], check=True)
        environment = {"HMB_CODEX_FIXTURE_CASE": "router-facts",
                       "HMB_CODEX_FIXTURE_STATE": str(self.directory / "native-state.json")}
        select_fixture(environment, self.directory, "codex", native)
        with self.daemon(env=environment):
            client = BoardClient(self.directory, autostart=False)
            submitted = client.workflow_submit(cwd=str(work), title="Private route",
                task="Read frozen routing facts", macroTaskId="standalone", buddy="route",
                checkout="existing", access="read", inputs=[], expectedSeconds=1800,
                creationKey="a" * 64)
            routed = client.workflow_get(run_id=submitted["runId"])
            request_id = routed["routing"]["decisionId"]

            def completed():
                value = client.call("selection_get", {"decisionId": request_id, "includeAudit": True})["decision"]
                return value if value["status"] not in ("queued", "running") else None

            decision = wait_for(completed, timeout=30)
            self.assertEqual(decision["status"], "completed", decision)
            packet = decision["input"]
            self.assertEqual(packet["expectedSeconds"], 1800)
            for buddy in packet["buddies"]:
                self.assertEqual(buddy["statistics"]["overall"]["samples"], 0)
                self.assertEqual(buddy["statistics"]["overall"]["usage"]["samples"], 0)
                self.assertEqual(buddy["infrastructureFailures"], {"count": 0, "lastAt": None})
                self.assertIn("kind", buddy["billing"])
                self.assertIn("wait", buddy["load"])
            self.assertEqual(decision["evidence"], [{"kind": "statistics", "ref": ids[0]}])
