"""Scheduling constants: model families, shared limits and pool identity.

ADR-011 removed the separate business/decision lanes. These unit tests pin the
family rule (exact adapter/provider/model tuple, no effort dimension), the shared
limit bounds and the stable managed-pool IDs that the daemon records.
"""
from __future__ import annotations

import unittest

from support import BoardTestCase  # noqa: F401 - ensures the source tree is importable

from buddy import scheduling


class ModelFamilyTests(unittest.TestCase):
    def test_family_is_the_exact_adapter_provider_model_tuple(self):
        spec = {"adapter": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off"}
        self.assertEqual(
            scheduling.model_family(spec),
            ("dsh", "deepseek-official", "deepseek-flash"),
        )

    def test_effort_never_widens_or_narrows_the_family(self):
        base = {"adapter": "dsh", "provider": "deepseek-official", "model": "deepseek-flash"}
        families = {scheduling.model_family({**base, "effort": effort}) for effort in ("off", "low", "high", "max")}
        self.assertEqual(families, {("dsh", "deepseek-official", "deepseek-flash")})

    def test_partial_configurations_and_model_less_work_have_no_family(self):
        for spec in (
            {"adapter": "command", "cwd": "/tmp", "argv": ["/bin/true"]},
            {"adapter": "external", "cwd": "/tmp"},
            {"adapter": "command", "provider": "p", "model": "incidental-metadata"},
            {"adapter": "external", "provider": "p", "model": "incidental-metadata"},
            {"adapter": "decision", "provider": "p", "model": "not-the-frozen-selector"},
            {"adapter": "dsh", "provider": "deepseek-official"},  # no model
            {"adapter": "dsh", "model": "deepseek-flash"},  # no provider
            {"adapter": "unresolved", "cwd": "/tmp"},
            {"cwd": "/tmp"},
            None,
            ["not", "a", "spec"],
        ):
            self.assertIsNone(scheduling.model_family(spec), spec)

    def test_non_string_family_fields_are_not_a_family(self):
        self.assertIsNone(scheduling.model_family({"adapter": "dsh", "provider": 1, "model": "m"}))
        self.assertIsNone(scheduling.model_family({"adapter": "dsh", "provider": "p", "model": ""}))


class LimitBoundsTests(unittest.TestCase):
    def test_total_ceiling_defaults_to_eight_and_clamps_one_to_thirty_two(self):
        self.assertEqual(scheduling.TOTAL_CONCURRENCY_DEFAULT, 8)
        self.assertEqual(scheduling.clamp_total(0), 1)
        self.assertEqual(scheduling.clamp_total(1), 1)
        self.assertEqual(scheduling.clamp_total(8), 8)
        self.assertEqual(scheduling.clamp_total(32), 32)
        self.assertEqual(scheduling.clamp_total(33), 32)
        self.assertEqual(scheduling.clamp_total(-5), 1)

    def test_model_limit_defaults_to_two_and_clamps_one_to_thirty_two(self):
        self.assertEqual(scheduling.MODEL_LIMIT_DEFAULT, 2)
        self.assertEqual(scheduling.clamp_model_limit(1), 1)
        self.assertEqual(scheduling.clamp_model_limit(2), 2)
        self.assertEqual(scheduling.clamp_model_limit(32), 32)
        self.assertEqual(scheduling.clamp_model_limit(33), 32)
        self.assertEqual(scheduling.clamp_model_limit(0), 1)

    def test_queue_reasons_name_the_limit_that_holds_work(self):
        self.assertEqual(scheduling.REASON_TOTAL_CAPACITY, "capacity")
        self.assertEqual(scheduling.REASON_MODEL_CAPACITY, "model-capacity")


class PoolIdentityTests(unittest.TestCase):
    def test_pool_ids_cover_the_whole_machine_ceiling_under_one_prefix(self):
        self.assertEqual(scheduling.pool_worker_ids("local", 3), ["local", "local-2", "local-3"])
        self.assertEqual(len(scheduling.pool_worker_ids("local", 8)), 8)
        self.assertEqual(scheduling.pool_worker_ids("local", 1), ["local"])

    def test_invalid_prefix_falls_back_to_the_default_worker_id(self):
        self.assertEqual(scheduling.pool_worker_ids("..", 2), ["local", "local-2"])
        self.assertEqual(scheduling.pool_worker_ids(42, 2), ["local", "local-2"])

    def test_valid_worker_id_rejects_values_that_are_not_safe_directory_segments(self):
        self.assertIsNone(scheduling.valid_worker_id("."))
        self.assertIsNone(scheduling.valid_worker_id(".."))
        self.assertIsNone(scheduling.valid_worker_id("a/b"))
        self.assertIsNone(scheduling.valid_worker_id(7))
        self.assertEqual(scheduling.valid_worker_id("local-2"), "local-2")


if __name__ == "__main__":
    unittest.main()
