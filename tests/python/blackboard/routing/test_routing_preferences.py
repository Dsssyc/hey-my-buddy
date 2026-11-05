"""Current Host routing inputs, selection evidence and preference boundaries."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import ANY

from hey_my_buddy.protocol.requests import parse_request
from hey_my_buddy.buddy.harnesses.base import Adapter, ExecutionContext, ReadOnlyStructuredRequest
from hey_my_buddy.buddy.roles.router import DecisionAdapter
from hey_my_buddy.buddy.roles.prompts import router_prompt
from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
from hey_my_buddy.buddy.harnesses.codex.adapter import CodexAdapter
from hey_my_buddy.buddy.harnesses.claude.adapter import ClaudeAdapter
from hey_my_buddy.buddy.harnesses.zcode.adapter import ZcodeAdapter
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install.runtime import resource_path
from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from blackboard.routing.test_decision import DecisionTestCase, CATALOG_BUDDY, BUDDY_ID, SECOND_BUDDY, SECOND_BUDDY_ID, THIRD_BUDDY, THIRD_BUDDY_ID
from blackboard.evaluation.test_evaluation import model_service_key
from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase

NONCE = "n" * 16


class CurrentSubmissionInputTests(unittest.TestCase):
    """The Host and helper request models reject retired routing spellings."""

    def submit_params(self, root):
        return {"cwd": root, "macroTaskId": "standalone", "title": "Inspect checkout",
                "task": "Inspect this checkout", "buddy": "route", "checkout": "existing",
                "access": "write", "inputs": [], "writeScope": ["."],
                "expectedSeconds": 1800}

    def helper_params(self, root):
        return {"title": "Check dependency", "task": "Check a dependency", "cwd": root,
                "buddy": "route", "checkout": "existing", "access": "write",
                "inputs": [], "writeScope": ["."], "expectedSeconds": 1800}

    def parse_submit(self, params):
        return parse_request("submit", params, audience="service")

    def parse_helpers(self, helpers):
        return parse_request("continue", {"runId": "root", "requestId": "request",
                                          "decision": "approve", "autoContinue": True,
                                          "helpers": helpers}, audience="service")

    def test_nested_spec_is_rejected_at_both_entries(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(BoardError) as submitted:
                self.parse_submit({**self.submit_params(root), "spec": {"task": "nested"}})
            self.assertEqual(submitted.exception.details["field"], "spec")
            with self.assertRaises(BoardError) as helper:
                self.parse_helpers([{**self.helper_params(root), "spec": {"task": "nested"}}])
            self.assertIn("spec", helper.exception.details["field"])

    def test_retired_routing_and_authority_fields_are_rejected(self):
        removed = {"routingPreferences": [], "routingMode": "fast", "allowRoutingFallback": False,
                   "requiredCapabilities": ["effort:high"], "exclusiveResources": ["shared"],
                   "buddyLocked": True, "owner": "owner", "hostId": "host-1",
                   "submissionToken": "s" * 32, "requestId": "request",
                   "executionWorkspace": {"kind": "existing", "access": "write"}}
        with tempfile.TemporaryDirectory() as root:
            for key, value in removed.items():
                with self.subTest(field=key), self.assertRaises(BoardError) as raised:
                    self.parse_submit({**self.submit_params(root), key: value})
                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
                self.assertEqual(raised.exception.details["field"], key)

    def test_helper_packet_has_no_inherited_preference_or_integrator_role(self):
        with tempfile.TemporaryDirectory() as root:
            helper = self.helper_params(root)
            for key, value in (("inheritRoutingPreferences", True), ("integrator", True),
                               ("role", "integrator"), ("requestId", "caller-helper"),
                               ("requiredCapabilities", ["effort:high"])):
                with self.subTest(field=key), self.assertRaises(BoardError) as raised:
                    self.parse_helpers([{**helper, key: value}])
                self.assertIn(key, raised.exception.details["field"])
            approved = self.parse_helpers([helper, {**helper, "title": "another helper"}])
            self.assertEqual(len(approved.helpers), 2)

    def test_complete_buddy_name_and_explicit_required_fields(self):
        with tempfile.TemporaryDirectory() as root:
            submitted = self.parse_submit({**self.submit_params(root), "buddy": " / ".join(BUDDY.values())})
            self.assertEqual(submitted.buddy, " / ".join(BUDDY.values()))
            for missing in ("buddy", "checkout", "access", "expectedSeconds", "macroTaskId"):
                params = self.submit_params(root)
                del params[missing]
                with self.subTest(field=missing), self.assertRaises(BoardError) as raised:
                    self.parse_submit(params)
                self.assertIn(missing, raised.exception.message)

    def test_execution_adapter_fields_are_rejected_at_both_entries(self):
        with tempfile.TemporaryDirectory() as root:
            for key, value in (("adapter", "command"), ("argv", ["/usr/bin/true"]),
                               ("harness", "dsh"), ("model", "deepseek-flash")):
                with self.subTest(field=key):
                    with self.assertRaises(BoardError) as submitted:
                        self.parse_submit({**self.submit_params(root), key: value})
                    self.assertEqual(submitted.exception.details["field"], key)
                    with self.assertRaises(BoardError) as helper:
                        self.parse_helpers([{**self.helper_params(root), key: value}])
                    self.assertIn(key, helper.exception.details["field"])


class DecisionCapabilityTests(unittest.TestCase):
    def test_a_declared_level_and_native_readiness_are_both_required(self):
        from hey_my_buddy.protocol.capabilities import PERMISSION_LEVELS
        native = SimpleNamespace(name="dsh", available=lambda: (True, None))
        for levels, ready, expected in (({}, True, False), ({"none": "enforced"}, False, False), ({"none": "enforced"}, True, True)):
            native.available = lambda: (ready, None)
            with mock.patch.dict(PERMISSION_LEVELS, {"dsh": levels}), mock.patch('hey_my_buddy.buddy.harnesses.registry.adapters', return_value={"dsh": native}):
                self.assertEqual(DecisionAdapter().available()[0], expected)

    def test_decision_dispatches_generic_request_with_frozen_input_and_no_agent_authority(self):
        class Native(Adapter):
            name = "codex"

        calls = []
        def start(_name, context, request):
            calls.append((context, request))
            return SimpleNamespace()
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "attempt"
            frozen = Path(root) / "frozen"
            frozen.mkdir()
            manifest = {"inputTree": "frozen-tree", "manifestSha256": "manifest"}
            budget = router.budget("brief")
            stats = {"samples": 0, "firstPasses": 0, "failures": 0, "medianDeliverySeconds": None}
            document = {"domainNodes": [], "buddy": {"harness": "codex", "provider": "openai", "model": "fixture-model", "effort": "low"},
                        "executionWorkspace": manifest, "executionWorkspaceForm": "archived-inline-v1",
                        "budget": budget, "outputSchema": router.answer_schema([BUDDY_ID]),
                        "buddies": [{"buddyId": BUDDY_ID, "load": {"wait": {"seconds": 0, "atLeast": False}},
                                     "statistics": {"overall": stats, "byDomain": {}, "byDomainTaskType": {}}}],
                        "task": "bounded read"}
            context = ExecutionContext(task_id="microTask", attempt_id="attempt", generation=1,
                                       spec={"cwd": root, "routerBudgetSeconds": 60}, directory=directory,
                                       runtime={}, environment={"HMB_STATE_DIR": str(Path(root) / "state"),
                                           "HMB_RUNTIME_ROOT": str(Path(root) / "runtime"), "HMB_DEV_SOURCE": "1"}, decision_input=document,
                                       turn={"input": {}}, agent_credential="must-not-pass", session_record_path=None)
            with mock.patch("hey_my_buddy.buddy.roles.router_input.prepare", return_value=(frozen, "digest")) as prepare, mock.patch(
                "hey_my_buddy.buddy.harnesses.registry.adapter", return_value=Native()
            ), mock.patch("hey_my_buddy.buddy.roles.run_execution.start_review", side_effect=start):
                native_adapter = DecisionAdapter()
                native_adapter.prepare(context)
                handle = native_adapter.start(context)
            from hey_my_buddy.private_dirs import context_root
            private_root = context_root(context, "codex")
            prepare.assert_called_once_with(manifest, private_root, state_dir=Path(root) / "state", deadline=ANY)
            self.assertFalse(private_root.is_relative_to(directory))
            child, request = calls[0]
            self.assertIsInstance(request, ReadOnlyStructuredRequest)
            self.assertEqual(request.cwd, str(frozen))
            body = router.render_prompt(document)
            self.assertEqual(request.prompt, router_prompt(body))
            role = resource_path("roles.router").read_text(encoding="utf-8")
            self.assertTrue(request.prompt.startswith(role + "\n"))
            self.assertEqual(request.prompt[len(role) + 1:], body)
            self.assertEqual(request.output_schema, document["outputSchema"])
            self.assertEqual(request.budget, budget)
            self.assertIsNone(child.turn)
            self.assertIsNone(child.agent_credential)
            self.assertEqual(child.spec["model"], "fixture-model")
            self.assertEqual(handle.router_input, (manifest, frozen, "digest"))

    def test_router_prompt_carries_no_task_preference_slot(self):
        prompt = router.render_prompt({"domainNodes": [], "routingMode": "review", "task": "choose", "buddies": [],
                                       "policyFacts": {"hardConstraints": {}, "userPreferredBuddyIds": []}})
        self.assertNotIn("routingPreferences", prompt)


class RetiredRoutingInputWorkflowTests(WorkflowTestCase):
    seed = DecisionTestCase.seed
    use_helper = DecisionTestCase.use_helper

    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        self.use_helper()

    @staticmethod
    def annotations(board):
        catalog = board.call("model_catalog_refresh", {"requestId": "annotations-catalog"})
        board.console_call("user_policy_publish", {
            "commandId": "annotations-publish",
            "modelServiceNoteChanges": [{**model_service_key(CATALOG_BUDDY), "text": "Human preference for this workflow"}],
        })

    def routed(self, board, *, request_id, **extra):
        return self.submit(board, request_id=request_id,
                           task="Implement and verify the requested change",
                           cwd=str(self.workdir(request_id)),
                           buddy=extra.pop("buddy", "route"), **extra)

    def test_submit_rejects_the_retired_preference_and_mode_entries(self):
        board = self.board()
        self.annotations(board)
        self.seed(board)
        for key, value in (
            ("routingPreferences", [{"match": {"provider": "not-current"}, "reason": "Try this provider"}]),
            ("routingMode", "fast"),
            ("allowRoutingFallback", False),
        ):
            with self.subTest(key=key):
                with self.assertRaises(BoardError) as raised:
                    self.routed(board, request_id=f"rejected-{key}", **{key: value})
                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
                self.assertEqual(raised.exception.details["field"], key)

    def test_submit_rejects_the_nested_spec_and_duplicate_workspace_spellings(self):
        board = self.board()
        self.seed(board)
        with self.assertRaises(BoardError) as raised:
            self.routed(board, request_id="rejected-spec",
                        spec={"task": "nested", "cwd": str(self.workdir("nested"))})
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(raised.exception.details["field"], "spec")
        with self.assertRaises(BoardError) as wrapped:
            board.call("workflow_submit", {
                "cwd": str(self.workdir("rejected-workspace")), "macroTaskId": "standalone",
                "title": "rejected-workspace", "task": "Inspect routing", "buddy": "route",
                "checkout": "existing", "access": "write", "inputs": [], "writeScope": ["."],
                "expectedSeconds": 1800,
                "executionWorkspace": {"kind": "existing", "access": "write"},
            })
        self.assertEqual(wrapped.exception.details["field"], "executionWorkspace")

    def test_complete_named_buddy_delegates_directly(self):
        board = self.board()
        self.seed(board)
        delegated = self.routed(board, request_id="direct-buddy", buddy=BUDDY)
        self.assertEqual(delegated["routing"]["status"], "explicit")
        self.assertEqual(delegated["routing"]["source"], "original-explicit")
        self.assertEqual(delegated["buddy"], BUDDY)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 0)
            self.assertIsNone(connection.execute("SELECT current_routing_id FROM workflow_runs").fetchone()[0])
            event = json.loads(connection.execute(
                "SELECT payload_json FROM events WHERE kind='workflow.buddy_selected'").fetchone()[0])
        self.assertNotIn("routingPreferences", event)

    def test_router_input_and_views_no_longer_carry_preferences(self):
        board = self.board()
        self.annotations(board)
        self.seed(board)
        view = self.routed(board, request_id="route-plain")
        self.assertEqual(view["routing"]["status"], "queued")
        self.assertNotIn("routingPreferences", view["routing"])
        self.assertNotIn("preferenceOutcome", view["routing"])
        pass  # Admission is owned by the service.
        owned = self.claim(board, "router", run_id=view["routing"]["taskId"], claim_request_id="route-claim")
        document = owned["claim"]["decisionInput"]
        self.assertNotIn("routingPreferences", document)
        self.assertNotIn("taskPreference", document["policyFacts"])
        note = document["annotations"][0]
        self.assertEqual({key: note[key] for key in ("harness", "provider", "model", "text")},
                         {**model_service_key(CATALOG_BUDDY), "text": "Human preference for this workflow"})
        board.call('execution_result', {'attemptId': owned['claim']['attempt']['attemptId'], 'generation': owned['claim']['attempt']['generation'], 'nonce': NONCE, 'status': 'ok', 'shutdownConfirmed': True, 'result': {**claim_tool_receipt(owned['claim'], state_dir=board.directory), 'status': 'ok', 'operation': 'select', 'usage': {'elapsedMs': 100, 'toolCalls': 0}, 'stopEvidence': {'shutdownConfirmed': True, 'native': {'shutdownConfirmed': True}}, 'inputVerification': {'unchanged': True, 'snapshotSha256': 'fixture-digest', 'manifestSha256': document['executionWorkspace']['manifestSha256']}, 'decision': {"domainNodeId": None, "taskType": "change", 'buddyId': BUDDY_ID, 'note': 'fixture selection', 'evidence': []}}})
        routed = board.call("workflow_get", {"runId": view["runId"]})
        self.assertEqual(routed["routing"]["source"], "model-selection")
        self.assertNotIn("routingPreferences", routed["routing"])
        self.assertNotIn("preferenceOutcome", routed["routing"])
        with board.store.db.read() as connection:
            requested = json.loads(connection.execute(
                "SELECT payload_json FROM events WHERE kind='workflow.routing_requested'").fetchone()[0])
            micro_task = json.loads(connection.execute("SELECT spec_json FROM tasks").fetchone()[0])
        self.assertNotIn("routingPreferences", requested)
        self.assertNotIn("routingPreferences", micro_task)

    def test_permission_capability_and_user_alternative_are_audited(self):
        board = self.board()
        self.annotations(board)
        self.seed(board, buddies=(CATALOG_BUDDY, SECOND_BUDDY, THIRD_BUDDY),
                  preferences=[{"buddyId": THIRD_BUDDY_ID, "mode": "prefer", "note": "prefer flash-high"}])
        view = self.routed(board, request_id="route-caps")
        self.assertEqual(view["routing"]["requiredCapabilities"], ["permission:write"])
        self.assertEqual(view["routing"]["constraints"], {})
        pass  # Admission is owned by the service.
        owned = self.claim(board, "router", run_id=view["routing"]["taskId"], claim_request_id="cap-claim")
        document = owned["claim"]["decisionInput"]
        self.assertIn(SECOND_BUDDY_ID, [entry["buddyId"] for entry in document["buddies"]])
        self.assertIn(THIRD_BUDDY_ID, [entry["buddyId"] for entry in document["buddies"]])
        board.call('execution_result', {'attemptId': owned['claim']['attempt']['attemptId'], 'generation': owned['claim']['attempt']['generation'], 'nonce': NONCE, 'status': 'ok', 'shutdownConfirmed': True, 'result': {**claim_tool_receipt(owned['claim'], state_dir=board.directory), 'status': 'ok', 'operation': 'select', 'usage': {'elapsedMs': 100, 'toolCalls': 0}, 'stopEvidence': {'shutdownConfirmed': True, 'native': {'shutdownConfirmed': True}}, 'inputVerification': {'unchanged': True, 'snapshotSha256': 'fixture-digest', 'manifestSha256': document['executionWorkspace']['manifestSha256']}, 'decision': {"domainNodeId": None, "taskType": "change", 'buddyId': SECOND_BUDDY_ID, 'note': 'fixture selection', 'evidence': []}}})
        routed = board.call("workflow_get", {"runId": view["runId"]})
        self.assertEqual(routed["routing"]["status"], "completed")
        decision = board.call("selection_get", {"decisionId": view["routing"]["decisionId"]})["decision"]
        self.assertEqual(decision["policyCheck"]["userPreference"], "alternative")
        self.assertNotIn("taskPreference", decision["policyCheck"])

    def test_helper_approval_rejects_the_inheritance_flag(self):
        board = self.board()
        self.annotations(board)
        self.seed(board)
        parent = self.submit(board)
        self.register(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        current = board.call("workflow_get", {"runId": parent["runId"]})
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_continue", {"runId": parent["runId"],
                "requestId": current["activeRequest"]["requestId"], "decision": "approve",
                "autoContinue": True,
                "helpers": [{"title": "inherit-helper", "task": "Inspect first dependency",
                             "cwd": str(self.workdir("inherit-helper")), "buddy": " / ".join(BUDDY.values()),
                             "checkout": "worktree", "access": "write", "inputs": [],
                             "writeScope": ["."], "expectedSeconds": 1800,
                             "inheritRoutingPreferences": True}]})
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertIn("inheritRoutingPreferences", raised.exception.message)
        # A plain helper carries no preference slot in its micro task.
        approved = self.decide(board, current, current["activeRequest"]["requestId"],
                              helpers=[{"title": "plain-helper", "task": "Inspect second dependency",
                                        "cwd": str(self.workdir("plain-helper")),
                                        "checkout": "worktree", "access": "write"}])
        with board.store.db.read() as connection:
            children = {row["request_id"]: row["task_id"] for row in connection.execute(
                "SELECT request_id,task_id FROM tasks WHERE task_id IN (SELECT child_task_id FROM workflow_children WHERE parent_run_id=?)",
                (parent["runId"],),
            )}
        plain = board.call("workflow_get", {"runId": approved["children"][0]["taskId"], "includeAudit": True})
        self.assertNotIn("routingPreferences", plain["audit"]["microTask"])
        self.assertEqual(len(children), 1)

    def test_host_override_requires_reason_and_records_no_preferences(self):
        board = self.board()
        self.annotations(board)
        view = self.routed(board, request_id="route-override")
        self.assertEqual(view["routing"]["status"], "needs-host")
        # The shared fixture helper supplies a default reason for explicit
        # buddies; exercise the public request without that field.
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_continue", {"runId": view["runId"],
                "requestId": view["activeRequest"]["requestId"],
                "input": "keep going", "buddy": " / ".join(BUDDY.values())})
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        updated = self.continue_run(board, view, buddy=BUDDY, note="Host selected the installed buddy")
        self.assertEqual(updated["routing"]["source"], "host-override")
        self.assertNotIn("routingPreferences", updated["routing"])
        with board.store.db.read() as connection:
            row = connection.execute("SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.buddy_overridden'",
                                     (view["runId"],)).fetchone()
            payload = json.loads(row["payload_json"])
        self.assertEqual(payload["note"], "Host selected the installed buddy")
        self.assertNotIn("routingPreferences", payload)


if __name__ == "__main__":
    unittest.main()
