"""Scheduling constants: model services, shared limits and pool identity.

ADR-011 removed the separate business/decision lanes. These unit tests pin the
model service rule (exact harness/provider/model tuple, no effort dimension), the shared
queue reasons.
"""
from __future__ import annotations

import unittest

from support import BoardTestCase  # noqa: F401 - ensures the source tree is importable

from hey_my_buddy.blackboard.tasks import scheduling


class ModelServiceTests(unittest.TestCase):
    def test_model_service_is_the_exact_harness_provider_model_tuple(self):
        spec = {"harness": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off"}
        self.assertEqual(
            scheduling.model_service(spec),
            ("dsh", "deepseek-official", "deepseek-flash"),
        )

    def test_effort_never_widens_or_narrows_the_model_service(self):
        base = {"harness": "dsh", "provider": "deepseek-official", "model": "deepseek-flash"}
        model_services = {scheduling.model_service({**base, "effort": effort}) for effort in ("off", "low", "high", "max")}
        self.assertEqual(model_services, {("dsh", "deepseek-official", "deepseek-flash")})

    def test_partial_buddies_and_model_less_work_have_no_model_service(self):
        for spec in (
            {"adapter": "command", "cwd": "/tmp", "argv": ["/bin/true"]},
            {"adapter": "command", "provider": "p", "model": "incidental-metadata"},
            {"adapter": "decision", "provider": "p", "model": "not-the-frozen-selector"},
            {"harness": "dsh", "provider": "deepseek-official"},  # no model
            {"harness": "dsh", "model": "deepseek-flash"},  # no provider
            {"adapter": "unresolved", "cwd": "/tmp"},
            {"cwd": "/tmp"},
            None,
            ["not", "a", "spec"],
        ):
            self.assertIsNone(scheduling.model_service(spec), spec)

    def test_non_string_model_service_fields_are_not_a_model_service(self):
        self.assertIsNone(scheduling.model_service({"harness": "dsh", "provider": 1, "model": "m"}))
        self.assertIsNone(scheduling.model_service({"harness": "dsh", "provider": "p", "model": ""}))


class LimitBoundsTests(unittest.TestCase):
    def test_queue_reasons_name_the_limit_that_holds_work(self):
        self.assertEqual(scheduling.REASON_TOTAL_CAPACITY, "capacity")
        self.assertEqual(scheduling.REASON_MODEL_CAPACITY, "model-capacity")
