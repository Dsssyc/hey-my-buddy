"""Frozen Router domain evidence and atomic judgments, without model calls."""
import json
from unittest.mock import patch

from blackboard.routing.test_decision import DecisionTestCase, BUDDY_ID, SECOND_BUDDY_ID
from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from blackboard.tasks.test_workflow import WorkflowTestCase, NONCE
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.protocol.task_types import TASK_TYPES


class RouterDomainTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        DecisionTestCase.use_helper(self)
        self.board_ = self.board()
        DecisionTestCase.seed(self, self.board_)

    def seed_domains(self):
        task = self.board_.submit_execution(
            requestId="domain-maintenance", task="Synthetic maintenance", adapter="command",
            argv=["true"], cwd=str(self.workdir("maintenance")))["task"]["runId"]
        with self.board_.store.db.write() as db:
            now = self.board_.store.now()
            db.execute("INSERT INTO maintenance_runs VALUES('domains','manual',?,?,"
                       "'completed',NULL,NULL,?,?)", (BUDDY_ID, task, now, now))
            for node_id, name, parent, merged in (
                    ("systems", "Systems", None, None), ("ipc", "IPC", "systems", None),
                    ("writing", "Writing", None, None), ("old-ipc", "Old IPC", "systems", "ipc")):
                db.execute("INSERT INTO domain_nodes VALUES(?,?,?,?,?,'domains')",
                           (node_id, name, name + " expertise", parent, merged))
            db.execute("INSERT INTO kind_summaries VALUES('dsh','deepseek-flash','off',?, 'domains')",
                       ("Handles transport defects carefully; asks for clear ownership boundaries.",))
            db.execute("INSERT INTO kind_statistics VALUES('dsh','deepseek-flash','off',3,2,1,1,1)")
            db.execute("INSERT INTO kind_cell_statistics VALUES('dsh','deepseek-flash','off',"
                       "'old-ipc','debug',2,1,1,0,1)")

    def request(self, request_id="domain-route"):
        return self.board_.call("selection_request", {"requestId": request_id, "task": "Fix transport ownership"})

    def packet(self, request):
        return self.board_.call("selection_get", {"decisionId": request["decisionId"],
                                                  "includeAudit": True})["decision"]["input"]

    def answer(self, claim, *, buddy_id=BUDDY_ID, domain="ipc", task_type="debug"):
        document = claim["decisionInput"]
        return {**claim_tool_receipt(claim, state_dir=self.board_.directory),
                "status": "ok", "operation": "select", "usage": {"elapsedMs": 100, "toolCalls": 0},
                "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}},
                "inputVerification": {"unchanged": True, "snapshotSha256": "fixture-digest",
                                      "manifestSha256": (document.get("executionWorkspace") or {}).get("manifestSha256")},
                "decision": {"buddyId": buddy_id, "domainNodeId": domain, "taskType": task_type,
                             "note": "Relevant domain experience",
                             "evidence": [{"kind": "statistics", "ref": BUDDY_ID + " — IPC debug evidence"},
                                          {"kind": "summary", "ref": BUDDY_ID + " — careful ownership review"}]}}

    def report(self, claim, answer):
        return self.board_.call("execution_result", {
            "attemptId": claim["attempt"]["attemptId"], "generation": claim["attempt"]["generation"],
            "nonce": NONCE, "status": "ok", "shutdownConfirmed": True, "result": answer})

    def judgments(self):
        with self.board_.store.db.read() as db:
            return [dict(row) for row in db.execute("SELECT * FROM routing_judgments ORDER BY decision_id")]

    def test_packet_has_active_nodes_three_levels_summary_and_type_definitions(self):
        self.seed_domains()
        request = self.request()
        packet = self.packet(request)
        self.assertEqual(packet["domainNodes"], [{"nodeId": "ipc", "name": "IPC"},
                                                {"nodeId": "systems", "name": "Systems"},
                                                {"nodeId": "writing", "name": "Writing"}])
        self.assertEqual(packet["taskTypes"], [{"id": key, "definition": value} for key, value in TASK_TYPES.items()])
        first = next(buddy for buddy in packet["buddies"] if buddy["buddyId"] == BUDDY_ID)
        stats = first["statistics"]
        self.assertEqual(stats["overall"]["samples"], 3)
        self.assertEqual(set(stats["byDomain"]), {"ipc", "systems"})
        self.assertEqual(stats["byDomain"]["ipc"]["samples"], 2)
        self.assertEqual(set(stats["byDomainTaskType"]), {"ipc", "systems"})
        self.assertEqual(set(stats["byDomainTaskType"]["ipc"]), {"debug"})
        self.assertEqual(stats["byDomainTaskType"]["systems"]["debug"]["samples"], 2)
        self.assertIn("ownership", first["summary"])
        other = next(buddy for buddy in packet["buddies"] if buddy["buddyId"] == SECOND_BUDDY_ID)
        self.assertIsNone(other["summary"])
        self.assertEqual(other["statistics"]["byDomain"], {})
        self.assertEqual(other["statistics"]["byDomainTaskType"], {})
        self.assertEqual(packet["outputSchema"]["properties"]["domainNodeId"]["enum"],
                         ["ipc", "systems", "writing", None])
        self.assertEqual(packet["outputSchema"]["properties"]["taskType"]["enum"], list(TASK_TYPES))
        prompt = router.render_prompt(packet)
        for node in packet["domainNodes"]:
            self.assertIn(json.dumps(node, separators=(",", ":")), prompt.splitlines())
        self.assertNotIn('"nodeId":"old-ipc"', prompt)
        self.assertIn('"summary":"Handles transport defects carefully;', prompt)
        self.assertEqual(self.judgments(), [])
        # Changing maintenance facts after admission does not rewrite this request.
        with self.board_.store.db.write() as db:
            db.execute("UPDATE kind_summaries SET text='Later observations'")
            db.execute("UPDATE domain_nodes SET name='Renamed IPC' WHERE node_id='ipc'")
        self.assertEqual(self.packet(request), packet)

    def test_kind_statistics_and_summary_cross_providers_but_not_efforts(self):
        from hey_my_buddy.blackboard.routing.evidence import candidate_facts
        self.seed_domains()
        kind = {"harness": "dsh", "model": "deepseek-flash", "effort": "off"}
        rows = [{"buddy_id": "first", **kind, "provider": "deepseek-official"},
                {"buddy_id": "other-provider", **kind, "provider": "other"},
                {"buddy_id": "other-effort", **kind, "provider": "other", "effort": "high"}]
        with self.board_.store.db.read() as db:
            facts = candidate_facts(self.board_.store, db, rows, now=self.board_.store.now())
        self.assertEqual(facts["first"]["statistics"], facts["other-provider"]["statistics"])
        self.assertEqual(facts["first"]["summary"], facts["other-provider"]["summary"])
        self.assertIsNotNone(facts["first"]["summary"])
        self.assertIsNone(facts["other-effort"]["summary"])
        self.assertEqual(facts["other-effort"]["statistics"]["byDomain"], {})

    def test_micro_task_project_comes_from_original_project_not_execution_checkout(self):
        self.seed_domains()
        original = str(self.workdir("project"))
        submitted = self.board_.call("workflow_submit", {
            "requestId": "project-route", "hostId": "host", "task": "Fix transport ownership", "cwd": original,
            "executionWorkspace": {"kind": "worktree", "access": "write"}})
        packet = self.packet({"decisionId": submitted["routing"]["decisionId"]})
        self.assertEqual(packet["project"]["path"], original)
        view = self.board_.call("task_get", {"runId": submitted["runId"]})
        self.assertEqual(packet["project"], view["task"]["lineage"]["project"])
        from hey_my_buddy.protocol.workspace_manifest import resolve_manifest
        execution = resolve_manifest(self.board_.directory, packet["executionWorkspace"])
        self.assertNotEqual(packet["project"]["path"], execution["path"])
        self.assertIn('"project":', router.render_prompt(packet))

    def test_chosen_and_abstained_answers_record_judgment_without_classifying(self):
        self.seed_domains()
        decision_ids = []
        for index, (buddy_id, domain) in enumerate(((BUDDY_ID, "ipc"), (None, None))):
            request = self.request(f"judgment-{index}")
            decision_ids.append(request["decisionId"])
            claim = self.claim(self.board_, "router", run_id=request["runId"])["claim"]
            result = self.report(claim, self.answer(claim, buddy_id=buddy_id, domain=domain))
            self.assertEqual(result["decision"]["status"], "needs-host" if buddy_id is None else "completed")
            expected = {"decision_id": request["decisionId"], "domain_node_id": domain, "task_type": "debug"}
            self.assertIn(expected, self.judgments())
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM micro_task_classifications").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM domain_nodes").fetchone()[0], 4)
            self.assertEqual(db.execute("SELECT samples FROM kind_statistics").fetchone()[0], 3)
        decisions = {item["decisionId"]: item for item in self.board_.store.evaluation.console_core()["decisions"]}
        self.assertEqual(decisions[decision_ids[0]]["taskType"], "debug")
        self.assertEqual(decisions[decision_ids[0]]["domainNodeName"], "IPC")
        self.assertIsNone(decisions[decision_ids[1]]["domainNodeId"])

    def test_judgment_and_decision_roll_back_together_when_terminal_event_fails(self):
        self.seed_domains()
        request = self.request()
        claim = self.claim(self.board_, "router", run_id=request["runId"])["claim"]
        coordinator = self.board_.store.decisions
        original = coordinator._append_event

        def fail_after_terminal_event(db, kind, decision_id, payload):
            original(db, kind, decision_id, payload)
            if kind == "decision.completed":
                self.assertEqual(db.execute("SELECT COUNT(*) FROM routing_judgments").fetchone()[0], 1)
                raise RuntimeError("after-domain-judgment")

        with patch.object(coordinator, "_append_event", side_effect=fail_after_terminal_event):
            with self.assertRaisesRegex(RuntimeError, "after-domain-judgment"):
                self.report(claim, self.answer(claim))
        self.assertEqual(self.judgments(), [])
        self.assertEqual(self.board_.call("selection_get", {"decisionId": request["decisionId"]})["decision"]["status"], "running")
        self.assertEqual(self.report(claim, self.answer(claim))["decision"]["status"], "completed")
        self.assertEqual(len(self.judgments()), 1)

    def test_no_judgment_for_invalid_answer_or_sole_candidate(self):
        self.seed_domains()
        request = self.request()
        claim = self.claim(self.board_, "router", run_id=request["runId"])["claim"]
        self.report(claim, self.answer(claim, domain="old-ipc"))
        self.assertEqual(self.judgments(), [])
        self.board_.console_call("user_policy_publish", {
            "commandId": "only-one", "buddySettings": [{"buddyId": SECOND_BUDDY_ID, "enabled": False}]})
        sole = self.request("sole")
        self.assertEqual(sole["status"], "completed")
        self.assertEqual(self.judgments(), [])
