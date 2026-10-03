"""ADR-021 decision 5/17: the retired Host submission inputs are rejected.

Task-local routing preferences, the submission's routing mode and fallback
switch, the nested ``spec`` spelling and the duplicate ``executionWorkspace``
source/integrator fields no longer exist; partial coding quadruples are
rejected with the three remaining expressions. This suite keeps every retired
entry point covered by a rejection test (nothing is silently deleted), while
the parts of the old scenarios that survive — user pin/prefer/exclude, hard
capabilities, override audit, direct delegation and sole-candidate selection —
stay asserted under the new contract.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from hey_my_buddy.protocol import schemas
from hey_my_buddy.buddy.harnesses.base import Adapter, ExecutionContext, ReadOnlyStructuredRequest
from hey_my_buddy.buddy.roles.router import DecisionAdapter
from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
from hey_my_buddy.buddy.harnesses.codex.adapter import CodexAdapter
from hey_my_buddy.buddy.harnesses.claude.adapter import ClaudeAdapter
from hey_my_buddy.buddy.harnesses.zcode.adapter import ZcodeAdapter
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.errors import BoardError
from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from blackboard.routing.test_decision import DecisionTestCase, PROFILE, PROFILE_ID, SECOND_PROFILE, SECOND_PROFILE_ID, THIRD_PROFILE, THIRD_PROFILE_ID
from blackboard.evaluation.test_evaluation import family_key
from blackboard.tasks.test_workflow import CONFIGURATION, WorkflowTestCase

NONCE = "n" * 16


class RetiredSubmissionInputTests(unittest.TestCase):
    """The schema rejects every removed spelling at its entry, with guidance."""

    def submit_params(self, root):
        return {"requestId": "goal-1", "hostId": "host-1", "task": "Inspect this checkout", "cwd": root,
                "executionWorkspace": {"kind": "existing", "access": "write"}}

    def test_nested_spec_is_rejected_at_both_entries(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(BoardError) as raised:
                schemas.normalize_workflow_submit({**self.submit_params(root),
                    "spec": {"task": "nested task", "cwd": root, "timeoutSeconds": 600}})
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
            self.assertIn("flat", raised.exception.message)
            with self.assertRaises(BoardError) as raised:
                schemas.normalize_helpers({"helpers": [{
                    "requestId": "helper", "task": "Check a dependency", "cwd": root,
                    "executionWorkspace": {"kind": "existing", "access": "write"},
                    "spec": {"task": "nested helper"}}]})
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_duplicate_workspace_fields_are_rejected_and_derived_instead(self):
        with tempfile.TemporaryDirectory() as root:
            for duplicate in ("cwd", "integrator"):
                field = {"cwd": root} if duplicate == "cwd" else {"integrator": "host-1"}
                with self.subTest(duplicate=duplicate):
                    with self.assertRaises(BoardError) as raised:
                        schemas.normalize_workflow_submit({**self.submit_params(root),
                            "executionWorkspace": {"kind": "existing", "access": "write", **field}})
                    self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
                    self.assertIn(f"executionWorkspace.{duplicate}", raised.exception.message)
            # The normalized intent still carries both, derived from the top level.
            normalized = schemas.normalize_workflow_submit(self.submit_params(root))
            self.assertEqual(normalized["executionWorkspace"]["cwd"], str(Path(root).resolve()))
            self.assertEqual(normalized["executionWorkspace"]["integrator"], "host:host-1")
            owned = schemas.normalize_workflow_submit({**self.submit_params(root), "owner": "named-owner"})
            self.assertEqual(owned["executionWorkspace"]["integrator"], "host:host-1")
            helper = schemas.normalize_helpers({"helpers": [{
                "requestId": "helper", "task": "Check a dependency", "cwd": root,
                "executionWorkspace": {"kind": "existing", "access": "write"}}]})[0]
            self.assertEqual(helper["executionWorkspace"]["cwd"], str(Path(root).resolve()))
            self.assertEqual(helper["executionWorkspace"]["integrator"], "host")

    def test_owner_attribution_never_changes_the_host_or_submission_identity(self):
        with tempfile.TemporaryDirectory() as root:
            first = schemas.normalize_workflow_submit({**self.submit_params(root), "owner": "first-label"})
            second = schemas.normalize_workflow_submit({**self.submit_params(root), "owner": "second-label"})
            fingerprints = [schemas.workflow_request_fingerprint(
                item["spec"], item["executionWorkspace"], item["hostId"]
            ) for item in (first, second)]
            self.assertEqual(fingerprints[0], fingerprints[1])
            helper = schemas.normalize_helpers({"helpers": [{
                "requestId": "helper", "task": "Inspect a dependency", "cwd": root,
                "owner": "display-only", "executionWorkspace": {"kind": "existing"}
            }]}, host_id="actual-host")[0]
            self.assertEqual(helper["executionWorkspace"]["integrator"], "host:actual-host")

    def test_retired_routing_inputs_are_rejected_with_the_three_expressions(self):
        retired = {
            "routingPreferences": [{"match": {"adapter": "dsh"}, "reason": "prefer this route"}],
            "routingMode": "fast",
            "allowRoutingFallback": False,
        }
        with tempfile.TemporaryDirectory() as root:
            for key, value in retired.items():
                with self.subTest(key=key):
                    with self.assertRaises(BoardError) as raised:
                        schemas.normalize_workflow_submit({**self.submit_params(root), key: value})
                    self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
                    self.assertIn("retired Host routing input", raised.exception.message)
                    # The message names the three expressions that remain.
                    self.assertIn("quadruple", raised.exception.message)
                    self.assertIn("Router", raised.exception.message)
                    self.assertIn("task description", raised.exception.message)

    def test_helper_inheritance_flag_is_rejected_because_nothing_is_inherited(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(BoardError) as raised:
                schemas.normalize_helpers({"helpers": [{
                    "requestId": "helper", "task": "Check a dependency", "cwd": root,
                    "executionWorkspace": {"kind": "existing", "access": "write"},
                    "inheritRoutingPreferences": True}]})
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
            self.assertIn("inheritRoutingPreferences", raised.exception.message)

    def test_partial_quadruples_are_rejected_at_both_entries(self):
        partials = ({"adapter": "zcode"}, {"model": "candidate"}, {"adapter": "dsh", "effort": "off"},
                    {"provider": "deepseek-official", "model": "deepseek-flash"})
        with tempfile.TemporaryDirectory() as root:
            for partial in partials:
                with self.subTest(partial=partial):
                    with self.assertRaises(BoardError) as raised:
                        schemas.normalize_workflow_submit({**self.submit_params(root), **partial})
                    self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
                    self.assertIn("all-or-nothing", raised.exception.message)
                    self.assertIn("task description", raised.exception.message)
                    with self.assertRaises(BoardError):
                        schemas.normalize_helpers({"helpers": [{
                            "requestId": "helper", "task": "Check a dependency", "cwd": root,
                            "executionWorkspace": {"kind": "existing", "access": "write"}, **partial}]})
            # Both complete expressions normalize: the whole quadruple, and none of it.
            complete = schemas.normalize_workflow_submit({**self.submit_params(root), **CONFIGURATION})
            self.assertEqual(schemas.configuration_constraints(complete["spec"]), CONFIGURATION)
            empty = schemas.normalize_workflow_submit(self.submit_params(root))
            self.assertEqual(schemas.configuration_constraints(empty["spec"]), {})

    def test_command_infrastructure_keeps_its_minimal_special_case(self):
        with tempfile.TemporaryDirectory() as root:
            command = schemas.normalize_workflow_submit({**self.submit_params(root),
                "adapter": "command", "argv": ["/usr/bin/true"]})
            self.assertEqual(command["spec"]["adapter"], "command")
            with self.assertRaises(BoardError) as raised:
                schemas.normalize_workflow_submit({**self.submit_params(root),
                    "adapter": "command", "argv": ["/usr/bin/true"], "model": "some-model"})
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
            self.assertIn("names no buddy", raised.exception.message)


class DecisionCapabilityTests(unittest.TestCase):
    def test_fast_capability_and_native_readiness_are_distinct_from_review_eligibility(self):
        class Native(Adapter):
            name = 'fixture'
            read_only_structured = True
            def available(self):
                return self.ready, None
        native = Native()
        for implemented, ready, expected in ((False, True, False), (True, False, False), (True, True, True)):
            native.no_tool_structured, native.ready = implemented, ready
            with mock.patch('hey_my_buddy.buddy.harnesses.registry.adapters', return_value={'fixture': native}):
                self.assertEqual(DecisionAdapter().available()[0], expected)

    def test_unimplemented_review_is_ineligible_without_certificate_attributes(self):
        for native in (Adapter, DshAdapter, CodexAdapter, ClaudeAdapter, ZcodeAdapter):
            with self.subTest(native=native.name):
                self.assertFalse(hasattr(native(), 'read_only_structured_verified'))
        self.assertFalse(Adapter().local_read_only_check()['eligible'])
        self.assertFalse(DshAdapter().local_read_only_check()['eligible'])
        self.assertFalse(ZcodeAdapter().local_read_only_check()['eligible'])
        self.assertFalse(DshAdapter.read_only_structured)
        self.assertTrue(CodexAdapter.read_only_structured)
        self.assertTrue(ClaudeAdapter.read_only_structured)
        self.assertTrue(DshAdapter.no_tool_structured)
        self.assertTrue(ZcodeAdapter.no_tool_structured)
        self.assertFalse(ClaudeAdapter.no_tool_structured)

    def test_decision_dispatches_generic_request_with_frozen_input_and_no_agent_authority(self):
        class Native(Adapter):
            name = "codex"
            read_only_structured = True
            def local_read_only_check(self):
                return {'eligible': True}

            def start_read_only_structured(self, context, request):
                calls.append((context, request))
                return SimpleNamespace()

        calls = []
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "attempt"
            frozen = Path(root) / "frozen"
            frozen.mkdir()
            manifest = {"inputTree": "frozen-tree", "manifestSha256": "manifest"}
            budget = router.budget("brief")
            document = {"profile": {"adapter": "codex", "provider": "openai", "model": "fixture-model", "effort": "low"},
                        "executionWorkspace": manifest, "budget": budget, "outputSchema": router.answer_schema([PROFILE_ID]),
                        "profiles": [{"profileId": PROFILE_ID}], "task": "bounded read"}
            context = ExecutionContext(task_id="goal", attempt_id="attempt", generation=1,
                                       spec={"cwd": root, "timeoutSeconds": 60}, directory=directory,
                                       runtime={}, environment={"BUDDY_STATE_DIR": str(Path(root) / "state"),
                                           "BUDDY_RUNTIME_ROOT": str(Path(root) / "runtime"), "BUDDY_DEV_SOURCE": "1"}, decision_input=document,
                                       turn={"input": {}}, agent_credential="must-not-pass")
            with mock.patch("hey_my_buddy.buddy.roles.router_input.prepare", return_value=(frozen, "digest")) as prepare, mock.patch(
                "hey_my_buddy.buddy.harnesses.registry.adapter", return_value=Native()
            ):
                handle = DecisionAdapter().start(context)
            from hey_my_buddy.private_dirs import context_root
            private_root = context_root(context, "codex")
            prepare.assert_called_once_with(manifest, private_root)
            self.assertFalse(private_root.is_relative_to(directory))
            child, request = calls[0]
            self.assertIsInstance(request, ReadOnlyStructuredRequest)
            self.assertEqual(request.cwd, str(frozen))
            self.assertEqual(request.prompt, router.render_prompt(document))
            self.assertEqual(request.output_schema, document["outputSchema"])
            self.assertEqual(request.budget, budget)
            self.assertIsNone(child.turn)
            self.assertIsNone(child.agent_credential)
            self.assertEqual(child.spec["model"], "fixture-model")
            self.assertEqual(handle.router_input, (manifest, frozen, "digest"))

    def test_ineligible_capability_is_rejected_before_input_preparation_or_spawn(self):
        context = ExecutionContext(task_id="goal", attempt_id="attempt", generation=1,
                                   spec={}, directory=Path("/tmp/unused"), runtime={}, environment={},
                                   decision_input={"profile": {"adapter": "codex"}})
        with mock.patch("hey_my_buddy.buddy.harnesses.registry.adapter", return_value=CodexAdapter()), mock.patch(
            "hey_my_buddy.buddy.roles.router_input.prepare"
        ) as prepare, mock.patch.object(CodexAdapter, 'local_read_only_check', return_value={'eligible': False}), \
                mock.patch.object(CodexAdapter, "start_read_only_structured") as start:
            with self.assertRaises(BoardError) as raised:
                DecisionAdapter().start(context)
        self.assertEqual(raised.exception.code, "router-review-unsupported")
        prepare.assert_not_called()
        start.assert_not_called()

    def test_router_prompt_carries_no_task_preference_slot(self):
        prompt = router.render_prompt({"routingMode": "review", "task": "choose", "profiles": [],
                                       "policyFacts": {"hardConstraints": {}, "userPreferredProfileIds": []}})
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
        grant = board.console_call("evaluation_write_begin", {
            "requestId": "annotations-writer", "expectedRevision": catalog["tableRevision"], "kind": "human",
        })
        board.console_call("user_policy_publish", {
            "commandId": "annotations-publish", "writerId": grant["writerId"],
            "generation": grant["generation"], "writerToken": grant["writerToken"],
            "expectedRevision": grant["tableRevision"],
            "familyAnnotationChanges": [{**family_key(PROFILE), "text": "Human preference for this workflow"}],
        })

    def routed(self, board, *, request_id, **extra):
        params = {
            "requestId": request_id, "hostId": "host-1", "submissionToken": "submission-secret-1",
            "task": "Implement and verify the requested change", "cwd": str(self.workdir(request_id)),
            "executionWorkspace": {"kind": "existing", "access": "write"}, **extra,
        }
        result = board.call("workflow_submit", params)
        if result.get("control"):
            self.controls[result["runId"]] = result["control"]
        return result

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
                self.assertIn("retired Host routing input", raised.exception.message)

    def test_submit_rejects_the_nested_spec_and_duplicate_workspace_spellings(self):
        board = self.board()
        self.seed(board)
        with self.assertRaises(BoardError) as raised:
            self.routed(board, request_id="rejected-spec",
                        spec={"task": "nested", "cwd": str(self.workdir("nested"))})
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertIn("flat", raised.exception.message)
        for duplicate, field in (("cwd", str(self.workdir("dup"))), ("integrator", "host-1")):
            with self.subTest(duplicate=duplicate):
                with self.assertRaises(BoardError):
                    self.routed(board, request_id=f"rejected-{duplicate}",
                                executionWorkspace={"kind": "existing", "access": "write", duplicate: field})

    def test_submit_rejects_partial_quadruples_but_delegates_a_complete_buddy_directly(self):
        board = self.board()
        self.seed(board)
        with self.assertRaises(BoardError) as raised:
            self.routed(board, request_id="rejected-effort", effort="high")
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertIn("all-or-nothing", raised.exception.message)
        # The complete quadruple delegates directly: no routing task, explicit source.
        delegated = self.routed(board, request_id="direct-buddy", **CONFIGURATION)
        self.assertEqual(delegated["routing"]["status"], "explicit")
        self.assertEqual(delegated["routing"]["source"], "original-explicit")
        self.assertEqual(delegated["executionConfiguration"], CONFIGURATION)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 0)
            self.assertIsNone(connection.execute("SELECT current_routing_id FROM workflow_runs").fetchone()[0])
            event = json.loads(connection.execute(
                "SELECT payload_json FROM events WHERE kind='workflow.configuration_selected'").fetchone()[0])
        self.assertNotIn("routingPreferences", event)

    def test_router_input_and_views_no_longer_carry_preferences(self):
        board = self.board()
        self.annotations(board)
        self.seed(board)
        view = self.routed(board, request_id="route-plain")
        self.assertEqual(view["routing"]["status"], "queued")
        self.assertNotIn("routingPreferences", view["routing"])
        self.assertNotIn("preferenceOutcome", view["routing"])
        board.call("worker_register", {"workerId": "router", "adapter": "decision", "capabilities": ["decision"]})
        owned = self.claim(board, "router", run_id=view["routing"]["taskId"], claim_request_id="route-claim")
        document = owned["claim"]["decisionInput"]
        self.assertNotIn("routingPreferences", document)
        self.assertNotIn("taskPreference", document["policyFacts"])
        note = document["annotations"][0]
        self.assertEqual({key: note[key] for key in ("adapter", "provider", "model", "text")},
                         {**family_key(PROFILE), "text": "Human preference for this workflow"})
        board.call("worker_result", {
            "workerId": "router", "attemptId": owned["claim"]["attempt"]["attemptId"],
            "generation": owned["claim"]["attempt"]["generation"], "nonce": NONCE,
            "status": "ok", "shutdownConfirmed": True,
            "result": {**claim_tool_receipt(owned["claim"]), "status": "ok", "operation": "select", "tableRevision": document["tableRevision"],
                       "usage": {"elapsedMs": 100, "toolCalls": 0},
                       "zeroToolVerified": True,
                       "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}},
                       "inputVerification": {"unchanged": True, "snapshotSha256": "fixture-digest", "manifestSha256": document["executionWorkspace"]["manifestSha256"]},
                       "decision": {"profileId": PROFILE_ID, "reason": "fixture selection", "evidence": []}},
        })
        routed = board.call("workflow_get", {"runId": view["runId"]})
        self.assertEqual(routed["routing"]["source"], "model-selection")
        self.assertNotIn("routingPreferences", routed["routing"])
        self.assertNotIn("preferenceOutcome", routed["routing"])
        with board.store.db.read() as connection:
            requested = json.loads(connection.execute(
                "SELECT payload_json FROM events WHERE kind='workflow.routing_requested'").fetchone()[0])
            goal = json.loads(connection.execute("SELECT goal_json FROM workflow_runs").fetchone()[0])
        self.assertNotIn("routingPreferences", requested)
        self.assertNotIn("routingPreferences", goal)

    def test_capabilities_still_filter_and_user_alternative_is_audited(self):
        board = self.board()
        self.annotations(board)
        # Two effort-high profiles keep the Router path under the capability
        # filter; a single constrained candidate would be selected by the program.
        self.seed(board, profiles=(PROFILE, SECOND_PROFILE, THIRD_PROFILE),
                  preferences=[{"profileId": THIRD_PROFILE_ID, "mode": "prefer", "reason": "prefer flash-high"}])
        view = self.routed(board, request_id="route-caps", requiredCapabilities=["effort:high"])
        self.assertEqual(view["routing"]["requiredCapabilities"], ["effort:high"])
        self.assertEqual(view["routing"]["constraints"], {})
        board.call("worker_register", {"workerId": "router", "adapter": "decision", "capabilities": ["decision"]})
        owned = self.claim(board, "router", run_id=view["routing"]["taskId"], claim_request_id="cap-claim")
        document = owned["claim"]["decisionInput"]
        self.assertEqual(sorted(entry["profileId"] for entry in document["profiles"]),
                         sorted([SECOND_PROFILE_ID, THIRD_PROFILE_ID]))
        board.call("worker_result", {
            "workerId": "router", "attemptId": owned["claim"]["attempt"]["attemptId"],
            "generation": owned["claim"]["attempt"]["generation"], "nonce": NONCE,
            "status": "ok", "shutdownConfirmed": True,
            "result": {**claim_tool_receipt(owned["claim"]), "status": "ok", "operation": "select", "tableRevision": document["tableRevision"],
                       "usage": {"elapsedMs": 100, "toolCalls": 0},
                       "zeroToolVerified": True,
                       "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}},
                       "inputVerification": {"unchanged": True, "snapshotSha256": "fixture-digest", "manifestSha256": document["executionWorkspace"]["manifestSha256"]},
                       "decision": {"profileId": SECOND_PROFILE_ID, "reason": "fixture selection", "evidence": []}},
        })
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
            board.call("workflow_decide", {"runId": parent["runId"],
                "requestId": current["activeRequest"]["requestId"], "commandId": "helper-inherit",
                "expectedRevision": current["revision"], "decision": "approve", **self.control(parent),
                "helpers": [
                    {"requestId": "inherit-helper", "task": "Inspect first dependency", "cwd": str(self.workdir("inherit-helper")),
                     "executionWorkspace": {"kind": "existing", "access": "write"}, "inheritRoutingPreferences": True},
                ]})
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertIn("inheritRoutingPreferences", raised.exception.message)
        # A plain helper carries no preference slot in its goal.
        approved = board.call("workflow_decide", {"runId": parent["runId"],
            "requestId": current["activeRequest"]["requestId"], "commandId": "helper-plain",
            "expectedRevision": current["revision"], "decision": "approve", **self.control(parent),
            "helpers": [
                {"requestId": "plain-helper", "task": "Inspect second dependency", "cwd": str(self.workdir("plain-helper")),
                 "executionWorkspace": {"kind": "existing", "access": "write"}},
            ]})
        with board.store.db.read() as connection:
            children = {row["request_id"]: row["task_id"] for row in connection.execute(
                "SELECT request_id,task_id FROM tasks WHERE task_id IN (SELECT child_task_id FROM workflow_children WHERE parent_run_id=?)",
                (parent["runId"],),
            )}
        plain = board.call("workflow_get", {"runId": children["plain-helper"], "includeAudit": True})
        self.assertNotIn("routingPreferences", plain["audit"]["goal"])
        self.assertEqual(approved["children"][0]["taskId"], children["plain-helper"])

    def test_host_override_requires_reason_and_records_no_preferences(self):
        board = self.board()
        self.annotations(board)
        view = self.routed(board, request_id="route-override")
        self.assertEqual(view["routing"]["status"], "needs-host")
        # The shared fixture helper supplies a default reason for explicit
        # configurations; exercise the public request without that field.
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_continue", {"runId": view["runId"], "commandId": "override-no-reason",
                "expectedRevision": view["revision"], "input": "keep going", "helperPolicy": "keep",
                "configuration": CONFIGURATION, **self.control(view)})
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        updated = self.continue_run(board, view, configuration=CONFIGURATION, reason="Host selected the installed configuration")
        self.assertEqual(updated["routing"]["source"], "host-override")
        self.assertNotIn("routingPreferences", updated["routing"])
        with board.store.db.read() as connection:
            row = connection.execute("SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.configuration_overridden'",
                                     (view["runId"],)).fetchone()
            payload = json.loads(row["payload_json"])
        self.assertEqual(payload["reason"], "Host selected the installed configuration")
        self.assertNotIn("routingPreferences", payload)


if __name__ == "__main__":
    unittest.main()
