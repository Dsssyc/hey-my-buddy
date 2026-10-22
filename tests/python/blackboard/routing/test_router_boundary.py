"""Host boundary facts and existing CLI templates without dispatching work."""
import unittest

from hey_my_buddy.blackboard.routing.router_boundary import build_boundary

CANDIDATE = {"buddyId": "dsh:fixture:model:max", "harness": "dsh", "provider": "fixture",
             "model": "model", "effort": "max", "contextWindow": 64000}
RETRY = "2026-01-01T00:10:00.000Z"


class RouterBoundaryTests(unittest.TestCase):
    def build(self, **changes):
        return build_boundary(**(dict(kind="router-unavailable", code="router-no-answer",
            reason={"code": "router-no-answer", "params": {}}, candidates=[CANDIDATE], facts={"policyFacts": {"candidateCount": 1}},
            router_trials=[{"buddyId": "router-a", "outcome": "no_answer", "code": "timeout"}],
            retry_at=RETRY, decision_id="dec-1", run_id="run-1", revision=7, shutdown_confirmed=True) | changes))

    def test_three_boundary_natures_and_model_reason_remain_distinct(self):
        for kind in ("router-unavailable", "routing-changed", "router-abstained"):
            with self.subTest(kind=kind):
                reason = {"code": "router-no-answer", "params": {}}
                result = self.build(kind=kind, reason=reason)
                self.assertEqual(result["kind"], kind)
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["userAction"], "settingsChangeOnly")

    def test_templates_keep_the_same_micro_task_and_complete_identity_with_supported_parameters(self):
        result = self.build()
        choice = result["commands"]["continue"]["choices"][0]
        params = choice["params"]
        self.assertEqual(params["runId"], "run-1")
        self.assertEqual(params["expectedRevision"], 7)
        self.assertEqual(params["controlFile"], "<saved-control-file>")
        self.assertEqual(params["buddy"], {key: CANDIDATE[key] for key in ("harness", "provider", "model", "effort")})
        self.assertNotIn("input", params)
        self.assertNotIn("note", params)
        self.assertNotIn("reason", params)
        reroute = result["commands"]["reroute"]
        self.assertEqual(reroute["notBefore"], RETRY)
        self.assertEqual(reroute["method"], "continue")
        self.assertTrue(reroute["params"]["reroute"])
        self.assertNotIn("notBefore", reroute["params"])
        self.assertNotIn("input", reroute["params"])
        self.assertNotIn("note", reroute["params"])
        self.assertNotIn("reason", reroute["params"])
        # Validate the public parameter vocabulary, without a control-file read
        # or any service/model call. The Host supplies a note when selecting a buddy.
        from hey_my_buddy.cli.cli_help import method_help
        help_ = method_help("continue")
        self.assertTrue(help_)
        fields = {item.name for item in help_.parameters}
        # controlFile is a CLI-local authority source, consumed before the
        # operation validator whose vocabulary this help extracts. The Host
        # supplies input and, when selecting a buddy, a note.
        self.assertTrue(set(params) - {"controlFile"} <= fields, set(params) - fields - {"controlFile"})
        self.assertTrue(set(reroute["params"]) - {"controlFile"} <= fields,
                        set(reroute["params"]) - fields - {"controlFile"})
        self.assertEqual(choice["method"], "continue")
        self.assertNotEqual(params["commandId"], reroute["params"]["commandId"])

    def test_stop_unknown_and_hard_gate_block_both_operations(self):
        for changes in ({"shutdown_confirmed": False}, {"continuation_problem": "workflow-routing-fenced"}):
            with self.subTest(changes=changes):
                result = self.build(**changes)
                for command in result["commands"].values():
                    self.assertTrue(command["blocked"])
                    self.assertNotIn("reason", command)
                    self.assertEqual(command["code"], changes.get("continuation_problem"))
                self.assertEqual(result["commands"]["continue"]["choices"], [])
                self.assertIsNone(result["commands"]["reroute"]["params"])

    def test_owned_descendant_templates_use_root_authority_and_preserve_helpers(self):
        result = self.build(run_id="root", revision=42, target_run_id="nested-child", helper_policy="keep")
        params = [result["commands"]["continue"]["choices"][0]["params"],
                  result["commands"]["reroute"]["params"]]
        from hey_my_buddy.cli.cli_help import method_help
        fields = {item.name for item in method_help("continue").parameters}
        for template in params:
            self.assertEqual(template["runId"], "root")
            self.assertEqual(template["expectedRevision"], 42)
            self.assertEqual(template["targetRunId"], "nested-child")
            self.assertEqual(template["helperPolicy"], "keep")
            self.assertEqual(template["controlFile"], "<saved-control-file>")
            self.assertTrue(set(template) - {"controlFile"} <= fields)

    def test_command_ids_are_stable_unique_bounded_and_change_with_owner_revision(self):
        candidate_b = dict(CANDIDATE, buddyId="candidate-b", model="model-b")
        options = dict(decision_id="dec-" + "long-id" * 100, candidates=[CANDIDATE, candidate_b],
                       target_run_id="child", helper_policy="keep")
        first = self.build(**options)["commands"]
        self.assertEqual(first, self.build(**options)["commands"])
        ids = [choice["params"]["commandId"] for choice in first["continue"]["choices"]]
        ids.append(first["reroute"]["params"]["commandId"])
        self.assertEqual(len(set(ids)), len(ids))
        self.assertTrue(all(len(value) <= 128 for value in ids))
        for changes in ({"revision": 8}, {"run_id": "new-owner"}, {"target_run_id": "other-child"}):
            result = self.build(**(options | changes))["commands"]
            self.assertNotEqual(result["continue"]["choices"][0]["params"]["commandId"], ids[0])
            self.assertNotEqual(result["reroute"]["params"]["commandId"], ids[-1])

    def test_live_helper_policy_cannot_make_a_destructive_template(self):
        blocked = self.build(continuation_problem="workspace-ownership-lost")
        self.assertTrue(blocked["commands"]["continue"]["blocked"])
        self.assertTrue(blocked["commands"]["reroute"]["blocked"])

    def test_unknown_recovery_time_allows_explicit_candidate_but_blocks_reroute_template(self):
        result = self.build(retry_at=None)
        self.assertFalse(result["commands"]["continue"]["blocked"])
        self.assertTrue(result["commands"]["reroute"]["blocked"])
        self.assertIsNone(result["retryAt"])
        self.assertIsNone(result["commands"]["reroute"]["code"])

    def test_standalone_selection_has_no_invented_business_commands(self):
        result = self.build(run_id=None, revision=None)
        self.assertEqual(result["commands"]["continue"]["choices"], [])
        self.assertTrue(result["commands"]["continue"]["blocked"])
        self.assertIsNone(result["commands"]["reroute"]["params"])
        self.assertIsNone(result["commands"]["reroute"]["code"])

    def test_derivable_gates_have_no_reason_code(self):
        for changes in ({"run_id": None}, {"revision": None}, {"shutdown_confirmed": False}, {"candidates": []}):
            with self.subTest(changes=changes):
                result = self.build(**changes)
                self.assertTrue(result["commands"]["continue"]["blocked"])
                for command in result["commands"].values():
                    self.assertNotIn("reason", command)
                    self.assertIsNone(command["code"])

    def test_frozen_candidates_facts_and_trials_are_not_mutated_or_aliased(self):
        facts = {"policyFacts": {"candidateCount": 1}}
        trials = [{"buddyId": "router-a", "outcome": "no_answer"}]
        candidates = [dict(CANDIDATE)]
        result = self.build(facts=facts, router_trials=trials, candidates=candidates)
        result["facts"]["policyFacts"]["candidateCount"] = 99
        result["routerTrials"][0]["outcome"] = "answered"
        result["commands"]["continue"]["choices"][0]["params"]["buddy"]["model"] = "other"
        result["candidates"][0]["contextWindow"] = 1
        self.assertEqual(facts["policyFacts"]["candidateCount"], 1)
        self.assertEqual(trials[0]["outcome"], "no_answer")
        self.assertEqual(candidates[0], CANDIDATE)

if __name__ == "__main__":
    unittest.main()
