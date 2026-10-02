"""Host boundary facts and existing CLI templates without dispatching work."""
import unittest

from buddy.errors import BoardError
from buddy.router_boundary import build_boundary

CANDIDATE = {"profileId": "dsh:fixture:model:max", "adapter": "dsh", "provider": "fixture",
             "model": "model", "effort": "max", "contextWindow": 64000}
RETRY = "2026-01-01T00:10:00.000Z"


class RouterBoundaryTests(unittest.TestCase):
    def build(self, **changes):
        return build_boundary(**(dict(kind="router-unavailable", code="router-unavailable",
            reason="Router 不可用：全部没有答案", candidates=[CANDIDATE], facts={"policyFacts": {"candidateCount": 1}},
            router_trials=[{"profileId": "router-a", "outcome": "no_answer", "code": "timeout"}],
            retry_at=RETRY, decision_id="dec-1", run_id="run-1", revision=7, shutdown_confirmed=True) | changes))

    def test_three_boundary_natures_and_model_reason_remain_distinct(self):
        for kind in ("router-unavailable", "routing-changed", "router-abstained"):
            with self.subTest(kind=kind):
                result = self.build(kind=kind, reason="原因原文")
                self.assertEqual(result["kind"], kind)
                self.assertEqual(result["reason"], "原因原文" if kind == "router-abstained" else "原因原文；取消后重新提交没有用。")
                self.assertEqual(result["userAction"], "settingsChangeOnly")

    def test_templates_keep_the_same_goal_and_complete_identity_with_supported_parameters(self):
        result = self.build()
        choice = result["commands"]["continue"]["choices"][0]
        params = choice["params"]
        self.assertEqual(params["runId"], "run-1")
        self.assertEqual(params["expectedRevision"], 7)
        self.assertEqual(params["controlFile"], "<saved-control-file>")
        self.assertEqual(params["configuration"], {key: CANDIDATE[key] for key in ("adapter", "provider", "model", "effort")})
        self.assertTrue(params["input"])
        self.assertTrue(params["reason"])
        reroute = result["commands"]["reroute"]
        self.assertEqual(reroute["notBefore"], RETRY)
        self.assertEqual(reroute["method"], "continue")
        self.assertTrue(reroute["params"]["reroute"])
        self.assertNotIn("notBefore", reroute["params"])
        # Validate the public parameter vocabulary, without a control-file read
        # or any service/model call. Required input/reason are in the templates.
        from buddy.cli_help import method_help
        help_ = method_help("continue")
        self.assertTrue(help_)
        fields = {item.name for item in help_.parameters}
        # controlFile is a CLI-local authority source, consumed before the
        # operation validator whose vocabulary this help extracts.
        self.assertTrue(set(params) - {"controlFile"} <= fields, set(params) - fields - {"controlFile"})
        self.assertTrue(set(reroute["params"]) - {"controlFile"} <= fields,
                        set(reroute["params"]) - fields - {"controlFile"})
        self.assertEqual(choice["method"], "continue")
        self.assertNotEqual(params["commandId"], reroute["params"]["commandId"])

    def test_stop_unknown_and_hard_gate_block_both_operations(self):
        for changes in ({"shutdown_confirmed": False}, {"continuation_problem": "固定 buddy 约束不满足"}):
            with self.subTest(changes=changes):
                result = self.build(**changes)
                for command in result["commands"].values():
                    self.assertTrue(command["blocked"])
                    self.assertTrue(command["reason"])
                self.assertEqual(result["commands"]["continue"]["choices"], [])
                self.assertIsNone(result["commands"]["reroute"]["params"])

    def test_unknown_recovery_time_allows_explicit_candidate_but_blocks_reroute_template(self):
        result = self.build(retry_at=None)
        self.assertFalse(result["commands"]["continue"]["blocked"])
        self.assertTrue(result["commands"]["reroute"]["blocked"])
        self.assertIsNone(result["retryAt"])

    def test_standalone_selection_has_no_invented_business_commands(self):
        result = self.build(run_id=None, revision=None)
        self.assertEqual(result["commands"]["continue"]["choices"], [])
        self.assertTrue(result["commands"]["continue"]["blocked"])
        self.assertIsNone(result["commands"]["reroute"]["params"])
        self.assertIn("独立 selection-request", result["commands"]["reroute"]["reason"])

    def test_frozen_candidates_facts_and_trials_are_not_mutated_or_aliased(self):
        facts = {"policyFacts": {"candidateCount": 1}}
        trials = [{"profileId": "router-a", "outcome": "no_answer"}]
        candidates = [dict(CANDIDATE)]
        result = self.build(facts=facts, router_trials=trials, candidates=candidates)
        result["facts"]["policyFacts"]["candidateCount"] = 99
        result["routerTrials"][0]["outcome"] = "answered"
        result["commands"]["continue"]["choices"][0]["params"]["configuration"]["model"] = "other"
        result["candidates"][0]["contextWindow"] = 1
        self.assertEqual(facts["policyFacts"]["candidateCount"], 1)
        self.assertEqual(trials[0]["outcome"], "no_answer")
        self.assertEqual(candidates[0], CANDIDATE)

    def test_incomplete_candidate_or_non_boolean_stop_evidence_cannot_make_a_template(self):
        for changes in ({"candidates": [{"profileId": "partial", "adapter": "dsh"}]},
                        {"shutdown_confirmed": 1}, {"revision": True}, {"kind": "failure"}):
            with self.subTest(changes=changes), self.assertRaises(BoardError):
                self.build(**changes)


if __name__ == "__main__":
    unittest.main()
