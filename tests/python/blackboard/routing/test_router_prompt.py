"""Derived Router evidence stays in the prompt, including the retained fast audit."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.roles import router as calls, run_execution, structured_call
from hey_my_buddy.json_codec import canonical_json


def candidate():
    def statistics(samples, first_passes, failures, median):
        return {"samples": samples, "successes": samples - failures, "firstPasses": first_passes,
                "failures": failures, "reworkRounds": 0,
                "firstPassRate": first_passes / samples if samples else None,
                "failureRate": failures / samples if samples else None,
                "medianDeliverySeconds": median}
    return {"buddyId": "legal", "load": {"active": 2, "limit": 2, "queued": 1,
            "wait": {"seconds": 300, "atLeast": True, "overdue": 1}},
            "statistics": {"overall": statistics(20, 16, 4, 120),
                           "byDomain": {"domain": statistics(10, 10, 0, 60)},
                           "byDomainTaskType": {"domain": {"review": statistics(4, 0, 4, None)}}}}


class RouterPromptTests(unittest.TestCase):
    def test_wilson_intervals_cover_empty_all_none_and_mixed_samples(self):
        self.assertIsNone(router._rate_interval(0, 0))
        for count, samples, lower, upper in (
                (0, 10, 0, 0.2775327998628892),
                (10, 10, 0.7224672001371107, 1),
                (4, 20, 0.080657662579798, 0.416017432251894)):
            with self.subTest(count=count, samples=samples):
                interval = router._rate_interval(count, samples)
                self.assertEqual(interval["method"], "wilson")
                self.assertEqual(interval["confidence"], 0.95)
                self.assertAlmostEqual(interval["lower"], lower, places=12)
                self.assertAlmostEqual(interval["upper"], upper, places=12)
                self.assertLessEqual(interval["lower"], count / samples + 1e-12)
                self.assertGreaterEqual(interval["upper"], count / samples - 1e-12)
        for count in (0, 1, 2):
            small = router._rate_interval(count, 2)
            large = router._rate_interval(count * 100, 200)
            self.assertLess(large["upper"] - large["lower"], small["upper"] - small["lower"])

    def test_each_level_adds_intervals_and_result_time_without_mutating_the_document(self):
        for mode in ("fast", "review"):
            for at_least in (False, True):
                with self.subTest(mode=mode, at_least=at_least):
                    buddy = candidate()
                    buddy["load"]["wait"]["atLeast"] = at_least
                    document = {"routingMode": mode, "buddies": [buddy]}
                    before = canonical_json(document)
                    rendered = json.loads(router.render_prompt(document).split("\n\n")[1])["buddies"][0]
                    stats = rendered["statistics"]
                    levels = ((stats["overall"], buddy["statistics"]["overall"], 420),
                              (stats["byDomain"]["domain"], buddy["statistics"]["byDomain"]["domain"], 360),
                              (stats["byDomainTaskType"]["domain"]["review"],
                               buddy["statistics"]["byDomainTaskType"]["domain"]["review"], None))
                    for enriched, original, seconds in levels:
                        for key, value in original.items():
                            self.assertEqual(enriched[key], value)
                        self.assertIn("firstPassRateInterval", enriched)
                        self.assertIn("failureRateInterval", enriched)
                        self.assertEqual(enriched["resultTime"], {"seconds": seconds, "atLeast": at_least,
                                                                "missingMedianDelivery": seconds is None})
                    self.assertAlmostEqual(stats["overall"]["failureRateInterval"]["upper"], 0.416017432251894)
                    self.assertAlmostEqual(stats["byDomain"]["domain"]["firstPassRateInterval"]["lower"], 0.7224672001371107)
                    self.assertEqual(canonical_json(document), before)

    def test_empty_samples_keep_rates_and_intervals_null_and_no_cells_are_invented(self):
        buddy = candidate()
        buddy["statistics"] = {"overall": {"samples": 0, "firstPasses": 0, "failures": 0,
                                           "firstPassRate": None, "failureRate": None,
                                           "medianDeliverySeconds": None},
                               "byDomain": {}, "byDomainTaskType": {}}
        prompt = router.render_prompt({"buddies": [buddy]})
        rendered = json.loads(prompt.split("\n\n")[1])["buddies"][0]["statistics"]
        for field in ("firstPassRate", "failureRate", "firstPassRateInterval", "failureRateInterval"):
            self.assertIsNone(rendered["overall"][field])
        self.assertIsNone(rendered["overall"]["resultTime"]["seconds"])
        self.assertTrue(rendered["overall"]["resultTime"]["missingMedianDelivery"])
        self.assertEqual(rendered["byDomain"], {})
        self.assertEqual(rendered["byDomainTaskType"], {})

    def test_fast_and_review_preparations_pass_derived_evidence_to_the_native_request(self):
        for mode in ("fast", "review"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                document = {"routingMode": mode, "task": "choose", "buddies": [candidate()],
                            "outputSchema": {"type": "object"}, "budget": {"timeoutSeconds": 60}}
                before = deepcopy(document)
                context = ExecutionContext("task", "attempt", 1, {}, root, {},
                                           {"HMB_STATE_DIR": str(root / "state")})
                prepare = calls.prepare_router_fast if mode == "fast" else calls.prepare_router_review
                preparation = prepare(document, {}, SimpleNamespace(name="dsh"), context)
                for key in ("firstPassRateInterval", "failureRateInterval", "resultTime"):
                    self.assertIn('"' + key + '":', preparation.request.prompt)
                self.assertEqual(document, before)

    def test_fast_call_audit_retains_the_rendered_prompt_with_derived_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = {"routingMode": "fast", "task": "choose", "buddies": [candidate()], "outputSchema": {}}
            before = canonical_json(document)
            context = ExecutionContext("task", "attempt", 1,
                                       {"provider": "fixture", "model": "fixture", "effort": "off"},
                                       root, {}, {"HMB_STATE_DIR": str(root / "state")})
            preparation = calls.prepare_router_fast(document, {}, SimpleNamespace(name="dsh"), context)
            handle = SimpleNamespace(role_run_control={"operation": "fast", "maintenance": False},
                                     exit_code=0, shutdown_confirmed=lambda: True)
            module = SimpleNamespace(check_preparation=lambda *_args: None)
            with patch.object(run_execution, "_launch", return_value=handle), \
                    patch.object(run_execution, "read_fast_result", return_value={"status": "ok"}):
                run_execution.start_fast(module, "dsh", preparation.context, preparation.request)
                self.assertEqual(structured_call.collect(handle).status, "ok")
            path = handle.no_tool_evidence.evidence_root / "call-1" / "request.json"
            audit = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(audit["prompt"], preparation.request.prompt)
            self.assertFalse(audit["captureEvidence"])
            for key in ("firstPassRateInterval", "failureRateInterval", "resultTime"):
                self.assertIn('"' + key + '":', audit["prompt"])
            self.assertEqual(canonical_json(document), before)


if __name__ == "__main__":
    unittest.main()
