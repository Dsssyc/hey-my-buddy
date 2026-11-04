"""Host routing boundary facts and actions without command templates."""
import unittest

from hey_my_buddy.blackboard.routing.router_boundary import build_boundary

CANDIDATE = {"buddyId": "dsh:fixture:model:max", "harness": "dsh", "provider": "fixture",
             "model": "model", "effort": "max", "contextWindow": 64000}
RETRY = "2026-01-01T00:10:00.000Z"


class RouterBoundaryTests(unittest.TestCase):
    def build(self, **changes):
        return build_boundary(**(dict(kind="router-unavailable", code="router-no-answer",
            reason={"code": "router-no-answer", "params": {}}, candidates=[CANDIDATE],
            facts={"policyFacts": {"candidateCount": 1}},
            router_trials=[{"buddyId": "router-a", "outcome": "no_answer", "code": "timeout"}],
            retry_at=RETRY, decision_id="dec-1", run_id="run-1", shutdown_confirmed=True) | changes))

    def test_three_boundary_natures_keep_their_reason_and_identity(self):
        for kind in ("router-unavailable", "routing-changed", "router-abstained"):
            with self.subTest(kind=kind):
                reason = {"code": "router-no-answer", "params": {}}
                result = self.build(kind=kind, reason=reason)
                self.assertEqual(result["kind"], kind)
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["decisionId"], "dec-1")
                self.assertEqual(result["runId"], "run-1")
                self.assertEqual(result["actions"], ["continue", "cancel"])
                self.assertEqual(result["userAction"], "settingsChangeOnly")
                self.assertNotIn("commands", result)
                self.assertNotIn("revision", result)

    def test_stop_and_continuation_fences_control_the_actions(self):
        for changes in ({"shutdown_confirmed": False},
                        {"continuation_problem": "workflow-routing-fenced"},
                        {"continuation_problem": "workspace-ownership-lost"}):
            with self.subTest(changes=changes):
                result = self.build(**changes)
                self.assertEqual(result["actions"], ["get", "cancel"])
                self.assertEqual(result["continuationProblem"], changes.get("continuation_problem"))
                self.assertEqual(result["candidates"], [CANDIDATE])

    def test_owned_descendant_keeps_the_root_and_target_ids(self):
        result = self.build(run_id="root", target_run_id="nested-child", helper_policy="keep")
        self.assertEqual(result["runId"], "root")
        self.assertEqual(result["targetRunId"], "nested-child")
        self.assertEqual(result["helperPolicy"], "keep")
        self.assertEqual(result["actions"], ["continue", "cancel"])

    def test_retry_time_is_a_fact_without_changing_available_actions(self):
        result = self.build(retry_at=None)
        self.assertIsNone(result["retryAt"])
        self.assertEqual(result["actions"], ["continue", "cancel"])

    def test_standalone_selection_exposes_no_host_continuation(self):
        result = self.build(run_id=None)
        self.assertEqual(result["actions"], ["get", "cancel"])
        self.assertIsNone(result["runId"])

    def test_boundary_does_not_alias_frozen_facts_or_candidates(self):
        facts = {"policyFacts": {"candidateCount": 1}}
        trials = [{"buddyId": "router-a", "outcome": "no_answer"}]
        candidates = [dict(CANDIDATE)]
        result = self.build(facts=facts, router_trials=trials, candidates=candidates)
        result["facts"]["policyFacts"]["candidateCount"] = 99
        result["routerTrials"][0]["outcome"] = "answered"
        result["candidates"][0]["contextWindow"] = 1
        self.assertEqual(facts["policyFacts"]["candidateCount"], 1)
        self.assertEqual(trials[0]["outcome"], "no_answer")
        self.assertEqual(candidates[0], CANDIDATE)


if __name__ == "__main__":
    unittest.main()
