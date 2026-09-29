"""Task-local soft routing preferences and decision capability boundaries."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from buddy import schemas
from buddy.adapters.base import Adapter, ExecutionContext, ReadOnlyStructuredRequest
from buddy.adapters.decision import DecisionAdapter
from buddy.adapters.dsh import DshAdapter
from buddy.adapters.codex import CodexAdapter
from buddy.adapters.claude import ClaudeAdapter
from buddy.adapters.zcode import ZcodeAdapter
from buddy import router
from buddy.errors import BoardError
from test_decision import DecisionTestCase, PROFILE, PROFILE_ID, SECOND_PROFILE_ID
from test_evaluation import family_key
from test_workflow import CONFIGURATION, WorkflowTestCase

NONCE = "n" * 16


class RoutingPreferenceSchemaTests(unittest.TestCase):
    def test_soft_preferences_are_canonical_goal_input_without_hard_constraints(self):
        with tempfile.TemporaryDirectory() as root:
            spec = schemas.normalize_workflow_spec({
                "requestId": "goal-soft", "task": "Inspect this checkout", "cwd": root,
                "routingPreferences": [{"match": {"adapter": "zcode", "model": "candidate"}, "reason": "prefer this route for the task"}],
            })
            self.assertNotIn("adapter", schemas.configuration_constraints(spec))
            self.assertEqual(spec["routingPreferences"][0]["match"], {"adapter": "zcode", "model": "candidate"})
            self.assertEqual(schemas.normalize_workflow_spec({**spec, "requestId": "goal-soft"})["routingPreferences"], spec["routingPreferences"])

    def test_rejects_unbounded_or_unexplained_preferences(self):
        invalid = [[{"match": {"adapter": "dsh"}, "reason": "x"}] * 9,
                   [{"match": {}, "reason": "x"}],
                   [{"match": {"model": "m"}, "reason": ""}],
                   [{"match": {"efforts": "high"}, "reason": "x"}]]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(BoardError):
                schemas.normalize_routing_preferences(value)

    def test_helper_inheritance_must_be_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            base = {"requestId": "helper", "task": "Check a dependency", "cwd": root,
                    "executionWorkspace": {"kind": "existing", "cwd": root}}
            helper = schemas.normalize_helpers({"helpers": [base]})[0]
            self.assertFalse(helper["inheritRoutingPreferences"])
            inherited = schemas.normalize_helpers({"helpers": [{**base, "inheritRoutingPreferences": True}]})[0]
            self.assertTrue(inherited["inheritRoutingPreferences"])
            with self.assertRaises(BoardError):
                schemas.normalize_helpers({"helpers": [{**base, "inheritRoutingPreferences": True,
                    "routingPreferences": [{"match": {"adapter": "dsh"}, "reason": "explicit"}]}]})


class DecisionCapabilityTests(unittest.TestCase):
    def test_fast_capability_and_native_readiness_are_distinct_from_review_verification(self):
        class Native(Adapter):
            name = 'fixture'
            read_only_structured = True
            def available(self):
                return self.ready, None
        native = Native()
        for implemented, ready, expected in ((False, True, False), (True, False, False), (True, True, True)):
            native.no_tool_structured, native.ready = implemented, ready
            with mock.patch('buddy.adapters.adapters', return_value={'fixture': native}):
                self.assertEqual(DecisionAdapter().available()[0], expected)

    def test_native_verification_defaults_false_including_dsh(self):
        for native in (Adapter, DshAdapter, CodexAdapter, ClaudeAdapter, ZcodeAdapter):
            with self.subTest(native=native.name):
                self.assertFalse(native().read_only_structured_verified)
        self.assertFalse(DshAdapter.read_only_structured)
        self.assertTrue(CodexAdapter.read_only_structured)
        self.assertTrue(ClaudeAdapter.read_only_structured)
        self.assertTrue(DshAdapter.no_tool_structured)
        self.assertTrue(ZcodeAdapter.no_tool_structured)
        self.assertFalse(ClaudeAdapter.no_tool_structured)

    def test_decision_dispatches_generic_request_with_frozen_input_and_no_agent_authority(self):
        class Native(Adapter):
            name = "native-fixture"
            read_only_structured = True
            read_only_structured_verified = True

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
            document = {"profile": {"adapter": "native-fixture", "provider": "fixture", "model": "model", "effort": "off"},
                        "executionWorkspace": manifest, "budget": budget, "outputSchema": router.answer_schema([PROFILE_ID]),
                        "profiles": [{"profileId": PROFILE_ID}], "task": "bounded read"}
            context = ExecutionContext(task_id="goal", attempt_id="attempt", generation=1,
                                       spec={"cwd": root, "timeoutSeconds": 60}, directory=directory,
                                       runtime={}, environment={}, decision_input=document,
                                       turn={"input": {}}, agent_credential="must-not-pass")
            with mock.patch("buddy.router_input.prepare", return_value=(frozen, "digest")) as prepare, mock.patch(
                "buddy.adapters.adapter", return_value=Native()
            ):
                handle = DecisionAdapter().start(context)
            prepare.assert_called_once_with(manifest, directory)
            child, request = calls[0]
            self.assertIsInstance(request, ReadOnlyStructuredRequest)
            self.assertEqual(request.cwd, str(frozen))
            self.assertEqual(request.prompt, router.render_prompt(document))
            self.assertEqual(request.output_schema, document["outputSchema"])
            self.assertEqual(request.budget, budget)
            self.assertIsNone(child.turn)
            self.assertIsNone(child.agent_credential)
            self.assertEqual(child.spec["model"], "model")
            self.assertEqual(handle.router_input, (manifest, frozen, "digest"))

    def test_unverified_capability_is_rejected_before_input_preparation_or_spawn(self):
        context = ExecutionContext(task_id="goal", attempt_id="attempt", generation=1,
                                   spec={}, directory=Path("/tmp/unused"), runtime={}, environment={},
                                   decision_input={"profile": {"adapter": "codex"}})
        with mock.patch("buddy.adapters.adapter", return_value=CodexAdapter()), mock.patch(
            "buddy.router_input.prepare"
        ) as prepare, mock.patch.object(CodexAdapter, "start_read_only_structured") as start:
            with self.assertRaises(BoardError) as raised:
                DecisionAdapter().start(context)
        self.assertEqual(raised.exception.code, "UNSUPPORTED_ADAPTER")
        prepare.assert_not_called()
        start.assert_not_called()


class RoutingPreferenceWorkflowTests(WorkflowTestCase):
    seed = DecisionTestCase.seed
    use_helper = DecisionTestCase.use_helper
    valid_decision = DecisionTestCase.valid_decision

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

    def routed(self, board, *, request_id, preferences, **constraints):
        result = board.call("workflow_submit", {
            "requestId": request_id, "hostId": "host-1", "submissionToken": "submission-secret-1",
            "task": "Implement and verify the requested change", "cwd": str(self.workdir(request_id)),
            "executionWorkspace": {"kind": "existing", "access": "write"},
            "routingPreferences": preferences, **constraints,
        })
        if result.get("control"):
            self.controls[result["runId"]] = result["control"]
        return result

    def select(self, board, view, profile_id):
        board.call("worker_register", {"workerId": "router", "adapter": "decision", "capabilities": ["decision"]})
        owned = self.claim(board, "router", run_id=view["routing"]["taskId"], claim_request_id="route-claim")
        claim = owned["claim"]
        document = claim["decisionInput"]
        board.call("worker_result", {
            "workerId": "router", "attemptId": claim["attempt"]["attemptId"],
            "generation": claim["attempt"]["generation"], "nonce": NONCE,
            "status": "ok", "shutdownConfirmed": True,
            "result": {"status": "ok", "operation": "select", "tableRevision": document["tableRevision"],
                       "inputVerification": {"unchanged": True, "manifestSha256": document["executionWorkspace"]["manifestSha256"]},
                       "decision": self.valid_decision(document, profile_id)},
        })
        return document

    def test_soft_preference_falls_back_without_changing_goal_or_global_preferences(self):
        board = self.board()
        self.annotations(board)
        self.seed(board)
        prefs = [{"match": {"provider": "not-current"}, "reason": "Try this provider if available"}]
        view = self.routed(board, request_id="route-soft", preferences=prefs)
        document = self.select(board, view, PROFILE_ID)
        self.assertEqual(document["routingPreferences"], prefs)
        note = document["annotations"][0]
        self.assertEqual({key: note[key] for key in ("adapter", "provider", "model", "text")},
                         {**family_key(PROFILE), "text": "Human preference for this workflow"})
        self.assertTrue(note["revision"])
        self.assertTrue(note["updatedAt"])
        self.assertEqual(document["preferences"], [])
        routed = board.call("workflow_get", {"runId": view["runId"]})
        self.assertEqual(routed["routing"]["preferenceOutcome"]["status"], "fallback")
        self.assertEqual(routed["routing"]["source"], "model-selection")
        self.assertEqual(routed["routing"]["routingPreferences"], prefs)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM evaluation_preferences").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT reason FROM workflow_routes WHERE run_id=?", (view["runId"],)).fetchone()[0], "fixture selection")

    def test_direct_selection_request_freezes_preferences_in_decision_input(self):
        board = self.board()
        self.annotations(board)
        self.seed(board)
        prefs = [{"match": {"adapter": "dsh"}, "reason": "Use installed DSH when suitable"}]
        response = board.call("selection_request", {"requestId": "select-soft", "task": "Choose a coding profile",
                                                    "routingPreferences": prefs})
        self.assertEqual(response["status"], "queued")
        audit = board.call("selection_get", {"decisionId": response["decisionId"], "includeAudit": True})["decision"]
        self.assertEqual(audit["requested"]["routingPreferences"], prefs)
        with self.assertRaises(BoardError):
            board.call("selection_request", {"requestId": "select-soft", "task": "Choose a coding profile",
                                             "routingPreferences": [{"match": {"adapter": "codex"}, "reason": "Different request"}]})

    def test_hard_constraint_remains_filter_when_soft_preference_disagrees(self):
        board = self.board()
        self.annotations(board)
        self.seed(board)
        prefs = [{"match": {"effort": "off"}, "reason": "Try the cheaper effort"}]
        view = self.routed(board, request_id="route-hard", preferences=prefs, effort="high")
        document = self.select(board, view, SECOND_PROFILE_ID)
        self.assertEqual([entry["profileId"] for entry in document["profiles"]], [SECOND_PROFILE_ID])
        routed = board.call("workflow_get", {"runId": view["runId"]})
        self.assertEqual(routed["routing"]["preferenceOutcome"]["status"], "fallback")

    def test_legal_soft_preference_bypass_is_audited_as_alternative(self):
        board = self.board()
        self.annotations(board)
        self.seed(board)
        prefs = [{"match": {"model": "deepseek-flash"}, "reason": "Prefer the flash model"}]
        view = self.routed(board, request_id="route-alternative", preferences=prefs)
        self.select(board, view, SECOND_PROFILE_ID)
        routed = board.call("workflow_get", {"runId": view["runId"]})
        self.assertEqual(routed["routing"]["preferenceOutcome"]["status"], "alternative")
        self.assertEqual(routed["routing"]["preferenceOutcome"]["ruleIndex"], 0)

    def test_helper_gets_parent_soft_preferences_only_on_explicit_inherit(self):
        board = self.board()
        self.annotations(board)
        self.seed(board)
        prefs = [{"match": {"model": "deepseek-flash"}, "reason": "Same model for helper"}]
        parent = self.submit(board, routingPreferences=prefs)
        self.register(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        current = board.call("workflow_get", {"runId": parent["runId"]})
        approved = board.call("workflow_decide", {"runId": parent["runId"],
            "requestId": current["activeRequest"]["requestId"], "commandId": "helper-preference",
            "expectedRevision": current["revision"], "decision": "approve", **self.control(parent),
            "helpers": [
                {"requestId": "inherit-helper", "task": "Inspect first dependency", "cwd": str(self.workdir("inherit-helper")),
                 "executionWorkspace": {"kind": "existing", "access": "write"}, "inheritRoutingPreferences": True},
                {"requestId": "plain-helper", "task": "Inspect second dependency", "cwd": str(self.workdir("plain-helper")),
                 "executionWorkspace": {"kind": "existing", "access": "write"}},
            ]})
        with board.store.db.read() as connection:
            children = {row["request_id"]: row["task_id"] for row in connection.execute(
                "SELECT request_id,task_id FROM tasks WHERE task_id IN (SELECT child_task_id FROM workflow_children WHERE parent_run_id=?)",
                (parent["runId"],),
            )}
        inherited = board.call("workflow_get", {"runId": children["inherit-helper"], "includeAudit": True})
        plain = board.call("workflow_get", {"runId": children["plain-helper"], "includeAudit": True})
        self.assertEqual(inherited["audit"]["goal"]["routingPreferences"], prefs)
        self.assertNotIn("routingPreferences", plain["audit"]["goal"])

    def test_host_override_requires_reason_and_retains_original_preference_audit(self):
        board = self.board()
        self.annotations(board)
        prefs = [{"match": {"adapter": "zcode"}, "reason": "Try ZCode when available"}]
        view = self.routed(board, request_id="route-override", preferences=prefs)
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
        self.assertEqual(updated["routing"]["routingPreferences"], prefs)
        with board.store.db.read() as connection:
            row = connection.execute("SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.configuration_overridden'",
                                     (view["runId"],)).fetchone()
            payload = json.loads(row["payload_json"])
        self.assertEqual(payload["reason"], "Host selected the installed configuration")
        self.assertEqual(payload["routingPreferences"], prefs)

    def test_original_complete_configuration_records_explicit_source(self):
        board = self.board()
        self.annotations(board)
        view = self.submit(board, routingPreferences=[{"match": {"adapter": "dsh"}, "reason": "Prefer this"}])
        self.assertEqual(view["routing"]["source"], "original-explicit")


if __name__ == "__main__":
    unittest.main()
